# 开发与编译

## 环境

- Windows 10/11 x64
- Python 3.11 至 3.13
- PowerShell

## 本地运行

```powershell
git clone https://github.com/JiangXinMao/jiangmaobizhi.git
cd jiangmaobizhi
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python main.py
```

## 测试

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q
python -m compileall -q jiangmao_wallpaper main.py
```

测试不访问真实 API，也不需要密钥。

## 构建 EXE

```powershell
python -m PyInstaller --clean --noconfirm JiangMaoWallpaper.spec
```

输出位于 `dist\匠猫壁纸.exe`。这是单文件便携程序，不包含安装器。首次构建会较慢，后续可删除 `build\` 后进行干净重建。

## 构建配置

`JiangMaoWallpaper.spec` 负责收集 PySide6 图像插件、SVG 支持、Windows 锁屏依赖、图标和内置壁纸。新增运行时资源时，必须同步加入 `datas` 并运行打包烟雾测试：

```powershell
& '.\dist\匠猫壁纸.exe' --smoke-test
```
