# ModelSim 主链路仿真

## 环境

已验证：Windows、ModelSim Intel FPGA Starter 10.5b。旧版工具在本机不能处理中文工作目录，因此请把仓库放在纯英文路径。仓库不含工具安装包与编译工作库。

## 查看现有波形

进入 `simulation/`，双击 `打开波形.cmd`。它加载 `waveform.wlf` 并执行 `view.do`，按时钟、应用发送、线路发送、A/B 接收、判决、应用交付与计数分组。

也可在已设置 PATH 的终端执行：

```powershell
cd simulation
modelsim.exe -view waveform.wlf -do view.do
```

全程约 24.9µs。建议在约 15µs、24.7µs 放大，核对接收有效、判决、末字节与最终计数。

## 重新运行

先关闭正在读取该文件的波形窗口，再运行 `重新运行仿真.cmd`，或：

```powershell
vsim.exe -c -l simulation.log -do generate.do
```

`generate.do` 编译同目录源码与测试台，运行后导出 WLF/VCD 和日志。日志中应出现 `PASS tb_afdx_system delivered=2`。首次运行会创建 `work/` 编译库，该目录已在 Git 中忽略。

进一步检查末态：

```powershell
vsim.exe -c -view waveform.wlf -l verify_wave.log -do check_wave.do
Get-Content wave_counts.txt
```

应为 `FINAL_DELIVERED=2` 与 `FINAL_REJECTED=2`。示例文件 `wave_counts.txt` 为已经核实的结果。

## 对应逻辑

示例测试台将同一发送流送到 A/B 两路接收。两帧来自不同 VL，载荷分别为 17B 和 5B，检查载荷、长度、序列与交付数。测试台仅补充 10 拍收尾等待，保证最终寄存计数可被观察。

日志中的 `LINE_MBPS_AT_100MHZ` 是保留的假设换算值；本次实际时钟为 50MHz，对应 `LINE_MBPS_AT_50MHZ=100`。既有 IFG 注释按 8 拍配置书写，本例实际字节间隔为 4 拍。这些值不能用来声称电脑实际运行速度或板上测量性能。

## 两种源码布局

- `rtl/` 包含开发主线完整模块。
- `simulation/` 保留本例编译所需的平铺副本与参数化测试台，可独立运行。

修改设计时，请明确同步哪一套输入；原始副本与开发主线的换行格式可能不同。提交后应重新生成波形、日志和结果指纹，避免展示旧波形。
