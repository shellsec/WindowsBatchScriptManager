# PyInstaller 打包说明

## 准备工作

1. **安装 PyInstaller**：
   ```bash
   pip install pyinstaller
   ```

2. **安装依赖**（推荐）：
   ```bash
   pip install -r requirements.txt
   ```

## 打包方式

### 方式一：使用批处理脚本（推荐）

直接运行：
```bash
build_exe.bat
```

### 方式二：使用 spec 文件

```bash
pyinstaller build_exe.spec
```

### 方式三：命令行直接打包

```bash
pyinstaller --name="WindowsBatchScriptManager" --onefile --windowed --hidden-import=psutil --hidden-import=tkinter main.py
```

## 打包选项说明

- `--onefile`：打包成单个 exe 文件（推荐）
- `--windowed` 或 `--noconsole`：不显示控制台窗口（GUI 应用）
- `--hidden-import=psutil`：确保 psutil 被正确打包
- `--hidden-import=tkinter`：确保 tkinter 相关模块被正确打包
- `--name`：指定生成的 exe 文件名

## 打包后的文件位置

- **单文件模式**：`dist/WindowsBatchScriptManager.exe`
- **单目录模式**：`dist/WindowsBatchScriptManager/` 目录

## 重要说明

### 1. 配置文件位置

打包后的 exe 会在**同目录**下创建配置文件和 `logs/` 目录：
```
dist/
  ├── WindowsBatchScriptManager.exe
  ├── WindowsBatchScriptManager_scripts.json  (首次运行后自动创建)
  ├── execution_history.json  (首次记录历史后自动创建)
  ├── notification_config.json (首次配置通知后自动创建)
  ├── language_config.json     (首次切换语言后自动创建)
  └── logs/                    (首次记录日志时自动创建)
```

### 2. 路径处理

代码已自动处理 PyInstaller 打包后的路径问题：
- 使用 `get_app_path()` 获取正确的程序路径（在 exe 环境中返回 exe 路径）
- 所有配置文件保存在 exe 同目录（WindowsBatchScriptManager_scripts.json、execution_history.json、notification_config.json、language_config.json）
- 日志文件保存在 exe 同目录的 `logs/` 文件夹
- 语言字典硬编码在代码中，不依赖外部文件，完全兼容 PyInstaller

### 3. 依赖项

- **psutil**：可选，未安装时功能会降级（使用 netstat/tasklist）
- **tkinter**：Python 标准库，通常已包含

### 4. 常见问题

#### 问题 1：打包后无法运行
- 检查是否有隐藏导入缺失，使用 `--hidden-import` 添加
- 检查是否有数据文件缺失，使用 `--add-data` 添加
- **Windows Server 等环境**：打包时排除 win32com 相关模块（见 README「打包为 EXE」），避免 PyInstaller runtime hook 导致启动崩溃

#### 问题 2：打包后文件很大
- 使用 `--exclude-module` 排除不需要的模块
- 考虑使用 `--onedir` 模式（单目录）而不是 `--onefile`

#### 问题 3：杀毒软件误报
- 这是 PyInstaller 打包的常见问题
- 可以添加数字签名（需要证书）
- 或使用 `--key` 选项加密（需要安装 pyinstaller[encryption]）

#### 问题 4：启动速度慢
- `--onefile` 模式需要解压临时文件，启动较慢
- 可以使用 `--onedir` 模式提高启动速度

### 5. 测试建议

打包后建议测试以下功能：
- ✅ 启动和关闭程序
- ✅ 添加、编辑、删除脚本
- ✅ 启动、停止、重启脚本
- ✅ 配置文件保存和加载
- ✅ 日志记录功能
- ✅ 扫描运行中脚本
- ✅ 导出/导入配置
- ✅ 定时任务功能

### 6. 分发建议

- 将 exe 文件和 README.md 一起分发
- 首次运行时会在 exe 同目录创建 `WindowsBatchScriptManager_scripts.json`
- 用户可以将 exe 放在任意目录运行

## 高级选项

### 添加图标

如果有图标文件 `icon.ico`，在 spec 文件中设置：
```python
icon='icon.ico'
```

或在命令行添加：
```bash
--icon=icon.ico
```

### 单目录模式

如果希望启动更快，可以使用单目录模式：
```bash
pyinstaller --name="WindowsBatchScriptManager" --onedir --windowed main.py
```

### 排除模块

减少文件大小，排除不需要的模块：
```bash
--exclude-module=matplotlib --exclude-module=numpy
```
