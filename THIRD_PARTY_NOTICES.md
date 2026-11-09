# 第三方组件与素材

项目自有源代码和策略数据采用 MIT 许可，详见 LICENSE。随包组件保留各自许可和版权声明。

## 运行依赖

| 组件 | 上游许可 | 上游项目 |
| --- | --- | --- |
| PySide6 / Qt for Python | LGPL-3.0 / GPL-3.0 / 商业许可，按实际模块适用 | [Qt for Python](https://doc.qt.io/qtforpython-6/licenses.html) |
| MSS | MIT | [python-mss](https://github.com/BoboTiG/python-mss) |
| NumPy | BSD-3-Clause，二进制包内另含所用组件声明 | [NumPy 许可](https://numpy.org/doc/stable/license.html) |
| OpenCV / opencv-python-headless | OpenCV 为 Apache-2.0，Python 打包脚本为 MIT，二进制包含其他组件声明 | [opencv-python](https://github.com/opencv/opencv-python) |
| ONNX Runtime | MIT，二进制包内另含第三方声明 | [ONNX Runtime](https://github.com/microsoft/onnxruntime) |

Windows 免安装包通过 PyInstaller 构建。PyInstaller 使用 GPL-2.0-or-later 加引导程序分发例外。打包保留依赖附带的许可文件，并附带本项目 LICENSE、本文和 `LICENSES/` 下的 LGPLv3、GPLv3 文本。[PyInstaller 许可](https://pyinstaller.org/en/stable/license.html)

Qt 相关库以独立动态库随包提供。对应版本源码可从上游的版本标签取得，用户可在其许可允许的范围内替换兼容动态库。[PySide 源码](https://github.com/pyside/pyside-setup) · [Qt 源码](https://github.com/qt/qtbase)

## 识别资源

`src/lisha_overlay/assets/learned/model.onnx` 是本项目使用的便携 CPU 识别模型。配置文件记录模型指纹、数据来源和已知验证边界。当前仓库提供推理所需文件，训练素材和历史训练工作区在作者本地单独保存。

`assets/buffs/` 和 `assets/learned/` 中的游戏图案、界面字形与光标裁片用于识别参照。相关游戏画面和美术的权利归原权利人，项目 MIT 许可仅覆盖作者有权许可的内容。复用这些图像时，应自行确认实际使用所需授权。

策略库位于 `src/lisha_overlay/assets/strategy/`，由本项目离线模拟生成，用于应用内配平和本地策略实验页。
