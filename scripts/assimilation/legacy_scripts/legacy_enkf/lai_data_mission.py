import numpy as np
import pandas as pd
import subprocess
import xml.etree.ElementTree as ET
import os
import shutil
import time

# ==========================================
# 1. 配置区域 (请在此处修改你的路径和参数)
# ==========================================
CONFIG = {
    # APSIM 安装路径 (找到 Apsim.exe)
    'apsim_exe': r"F:\APSIM710-r4221\Model\Apsim.exe",

    # 你的工作目录 (所有生成的临时文件都会在这里)
    'work_dir': r"E:\A_汇报\毕设过渡\代码汇总\tonghua",

    # 你的基础 APSIM 模板文件 (必须配置好 Manager 和 Input 模块)
    'base_apsim_file': r"F:\APSIM710-r4221\yuan\rotation_fuben.apsim",

    # 观测数据文件 (CSV格式: Date, LAI, Error)
    'obs_data_file': r"E:\A_汇报\毕设过渡\代码汇总\data\Zouping_LAI_2014_2024.csv",

    # 集合成员数量
    'ensemble_size': 20,

    # 状态向量定义 [LAI, Biomass, SoilWater] (根据你的需求增减)
    # 注意：这里的顺序必须和下面 APSIM 读写的顺序一致
    'state_names': ['mean_LAI', 'biomass', 'sw_dep'],
}


# ==========================================
# 2. EnKF 算法类 (核心数学逻辑)
# ==========================================
class EnKF:
    def __init__(self, n_ens, dim_state, dim_obs):
        self.N = n_ens  # 集合大小
        self.n_x = dim_state  # 状态维度
        self.n_y = dim_obs  # 观测维度 (通常是1, 只观测LAI)

    def analysis(self, X_f, y_obs, R):
        """
        X_f: 预测状态集合 (n_x, N)
        y_obs: 观测值 (n_y, 1)
        R: 观测误差协方差 (n_y, n_y)
        """
        # 1. 观测算子 H (假设状态向量第1个元素是LAI)
        H = np.zeros((self.n_y, self.n_x))
        H[0, 0] = 1.0

        # 2. 集合平均与异常值
        X_mean = np.mean(X_f, axis=1, keepdims=True)
        A = X_f - X_mean

        # 3. 预测观测值
        Y_f = H @ X_f
        Y_mean = np.mean(Y_f, axis=1, keepdims=True)
        Y_perturb = Y_f - Y_mean

        # 4. 扰动观测值 (防止滤波发散)
        obs_noise = np.random.normal(0, np.sqrt(R[0, 0]), self.N)
        D_perturbed = y_obs + obs_noise

        # 5. 计算卡尔曼增益 K (使用集合形式)
        # S = Y_perturb @ Y_perturb.T / (N-1) + R
        # C = A @ Y_perturb.T / (N-1)
        # K = C * inv(S)

        S = (1.0 / (self.N - 1)) * np.dot(Y_perturb, Y_perturb.T) + R
        C = (1.0 / (self.N - 1)) * np.dot(A, Y_perturb.T)

        # 求解 K (处理标量或矩阵)
        try:
            K = np.dot(C, np.linalg.inv(S))
        except np.linalg.LinAlgError:
            K = C * (1.0 / S)  # 标量情况

        # 6. 更新状态
        # Innovation = D_perturbed - Y_f
        X_a = X_f + np.dot(K, (D_perturbed - Y_f))

        # 7. 物理约束 (LAI和生物量不能为负)
        X_a[X_a < 0] = 0.001

        return X_a


# ==========================================
# 3. APSIM 接口类 (文件操作与运行)
# ==========================================
class ApsimInterface:
    def __init__(self, config):
        self.cfg = config
        self.sim_files = []  # 存储每个成员的文件路径

        # 清理并创建工作目录
        if os.path.exists(self.cfg['work_dir']):
            shutil.rmtree(self.cfg['work_dir'])
        os.makedirs(self.cfg['work_dir'])

    def init_ensemble(self):
        """初始化集合：复制N份文件，并让每个文件读取自己对应的 txt"""
        print("正在初始化集合成员...")

        # 读取母版文件
        base_tree = ET.parse(self.cfg['base_apsim_file'])
        base_root = base_tree.getroot()

        for i in range(self.cfg['ensemble_size']):
            # --- 1. 修改 Input 模块的文件路径 (这就是你截图里问的核心操作) ---

            # 这里的路径说明：
            # .//input : 查找所有名为 input 的节点
            # [name='ExternalData'] : 筛选名字叫 ExternalData 的那个 input 组件
            # /filename : 找到它下面的 filename 标签
            input_node = base_root.find(".//input[@name='ExternalData']/filename")

            if input_node is not None:
                # 动态生成文件名：sim_0_update.txt, sim_1_update.txt ...
                new_txt_filename = f"sim_{i}.out"

                # 设置为绝对路径 (APSIM 最好用绝对路径，防止找不到)
                # 假设 txt 文件都放在 work_dir 下
                full_txt_path = os.path.join(self.cfg['work_dir'], new_txt_filename)

                # 修改 XML 内容
                input_node.text = full_txt_path
            else:
                print(f"警告：在 sim_{i} 中未找到名为 ExternalData 的 Input 模块！")

            # --- 2. 扰动其他参数 (如 RUE) ... (此处省略) ---

            # --- 3. 保存为新的 .apsim 文件 ---
            filename = f"sim_{i}.apsim"
            filepath = os.path.join(self.cfg['work_dir'], filename)
            base_tree.write(filepath)
            self.sim_files.append(filepath)

            # --- 4. 生成对应的空 txt 文件 ---
            # 必须马上生成这个文件，否则 APSIM 运行时如果找不到文件会直接报错
            initial_vec = [0, 0, 0]  # 初始 LAI, Biomass, SW
            # 这里日期设为很久以前，确保不会干扰当前模拟
            self.write_update_file(i, "1900-01-01", initial_vec)
    def write_update_file(self, member_idx, date_str, state_vec):
        """
        生成 APSIM Input 模块读取的文本文件
        内容：日期，LAI，Biomass，SoilWater
        """
        file_path = os.path.join(self.cfg['work_dir'], f"sim_{member_idx}.out")

        with open(file_path, 'w') as f:
            # 写表头 (必须与 APSIM Input 组件里的变量名匹配)
            f.write("update_date ext_lai ext_biom ext_sw\n")
            f.write("() (m2/m2) (kg/ha) (mm)\n")
            # 写入数据
            f.write(f"{date_str} {state_vec[0]:.3f} {state_vec[1]:.3f} {state_vec[2]:.3f}\n")

    def run_period(self, start_date, end_date):
        """运行所有成员从 start_date 到 end_date"""
        processes = []

        for i, sim_file in enumerate(self.sim_files):
            # 1. 修改 .apsim 文件中的起止时间
            tree = ET.parse(sim_file)
            root = tree.getroot()

            # 查找并修改 Clock 模块的时间
            root.find(".//start_date").text = start_date
            root.find(".//end_date").text = end_date
            tree.write(sim_file)

            # 2. 启动 APSIM 进程 (静默运行)
            # 注意：APSIM 7 默认是串行运行比较稳，如果报错可以去掉列表推导式改成逐个运行
            p = subprocess.Popen([self.cfg['apsim_exe'], sim_file],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            processes.append(p)

        # 等待所有进程结束
        for p in processes:
            p.wait()

    def get_forecast_states(self):
        """读取所有成员的 .out 文件最后一行"""
        X_forecast = []

        for i, sim_file in enumerate(self.sim_files):
            out_file = sim_file.replace(".apsim", ".out")

            try:
                # 读取最后一行
                df = pd.read_csv(out_file, sep='\s+', skiprows=[1])
                last_row = df.iloc[-1]

                # 提取状态 (必须与 CONFIG['state_names'] 对应)
                # 假设 .out 文件里输出列名为: 'lai', 'biomass', 'sw_dep'
                # 注意：APSIM输出的列名可能叫 'Wheat.lai'，需根据实际情况修改
                val_lai = last_row['mean_LAI']
                val_bio = last_row['biomass']
                val_sw = last_row['sw_dep']

                X_forecast.append([val_lai, val_bio, val_sw])
            except Exception as e:
                print(f"读取成员 {i} 失败: {e}")
                # 失败时用 0 填充或报错
                X_forecast.append([0, 0, 0])

        return np.array(X_forecast).T  # 转置为 (n_x, N)


# ==========================================
# 4. 主程序
# ==========================================
def main():
    # 1. 读取观测数据
    # 假设CSV格式: date, lai, error
    obs_df = pd.read_csv(CONFIG['obs_data_file'])
    print("CSV 文件包含的列名:", obs_df.columns)
    obs_df['date'] = pd.to_datetime(obs_df['date'])
    obs_df = obs_df.sort_values('date')

    # 2. 初始化
    model = ApsimInterface(CONFIG)
    model.init_ensemble()

    enkf = EnKF(CONFIG['ensemble_size'], 3, 1)  # 3个状态，1个观测

    # 设置模拟总起点 (比如由观测数据的第一天推前几个月)
    current_sim_date = "2014-6-01"

    print("开始 10 年连续同化模拟...")

    # 3. 循环每一个观测点
    # 3. 循环每一个观测点
    for idx, row in obs_df.iterrows():
        # --- 修复 1: 确保日期格式正确 ---
        # 如果你的 CSV date 是字符串，这里转换一下以防万一
        current_obs_time = pd.to_datetime(row['date'])
        target_date = current_obs_time.strftime('%Y/%m/%d')

        # --- 修复 2: 使用正确的列名 'mean_LAI' ---
        obs_val = row['mean_LAI']

        # --- 修复 3: 手动定义误差 (因为 CSV 里没有 'error' 列) ---
        # 方案 A: 设定为固定值 (比如 0.2 或 0.5)
        # obs_err = 0.5

        # 方案 B (推荐): 设定为观测值的 15%，并给一个最小值防止为0
        # 意思是：LAI越大，误差越大；但至少有 0.1 的误差
        obs_err = max(0.1, obs_val * 0.15)

        # ------------------------------------------------------

        # 只有当目标日期在当前日期之后才运行
        if pd.to_datetime(target_date) <= pd.to_datetime(current_sim_date):
            continue

        print(f"--- 阶段运行: {current_sim_date} -> {target_date} ---")

        # A. 运行模型到观测日
        model.run_period(current_sim_date, target_date)

        # B. 获取预测状态
        X_f = model.get_forecast_states()
        print(f"  预测均值: LAI={np.mean(X_f[0]):.2f}, Bio={np.mean(X_f[1]):.0f}")

        # C. 判断是否同化
        # 如果观测到的 LAI 太小(可能是裸土)，或者模型还没出苗，就跳过
        if obs_val > 0.1 and np.mean(X_f[0]) > 0:
            print(f"  执行同化: 观测LAI={obs_val:.2f}, 设定误差={obs_err:.2f}")

            # 准备矩阵
            y_obs = np.array([[obs_val]])
            R = np.array([[obs_err ** 2]])  # 注意：R矩阵里放的是方差，所以要平方

            # EnKF 更新
            X_a = enkf.analysis(X_f, y_obs, R)
            print(f"  分析均值: LAI={np.mean(X_a[0]):.2f}, Bio={np.mean(X_a[1]):.0f}")

            # D. 将更新后的状态写入文件
            for i in range(CONFIG['ensemble_size']):
                model.write_update_file(i, target_date, X_a[:, i])

        else:
            print("  非生长季或LAI过低，跳过同化")
            # 即使不同化，也要更新一下 update文件的时间，防止APSIM报错（可选）

        # E. 更新时间推进
        current_sim_date = target_date

    print("全部模拟结束。")


if __name__ == "__main__":
    main()