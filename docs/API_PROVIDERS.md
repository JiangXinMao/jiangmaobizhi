# 接入壁纸 API

公开版本默认只加载随程序发布的本地壁纸，不包含任何在线 API、账号、密钥、统计 SDK、远程公告或更新服务。联网能力通过 `WallpaperProvider` 扩展。

## 接入位置

- 数据模型：`jiangmao_wallpaper/models.py` 中的 `Wallpaper`
- Provider 协议与轮换链：`jiangmao_wallpaper/providers.py`
- 可运行模板：`examples/custom_provider.py`
- 默认注册入口：`default_provider_chain()`

Provider 必须提供稳定的 `name` 和以下方法：

```python
def fetch(self, count: int, market: str) -> list[Wallpaper]:
    ...
```

如需历史分页，可额外实现 `fetch_page(page, page_size) -> HistoryPage`。

## 注册与轮换

在 `default_provider_chain()` 中按优先级排列 Provider：

```python
def default_provider_chain() -> ProviderChain:
    return ProviderChain([
        MyPrimaryProvider(),
        MyFallbackProvider(),
        BundledWallpaperProvider(),
    ])
```

`ProviderChain.fetch()` 会按顺序请求。某个 Provider 抛出异常或返回空列表时，会继续下一个 Provider；第一个返回有效内容的 Provider 胜出。建议始终将 `BundledWallpaperProvider` 放在最后，确保断网可用。

## 密钥配置

密钥只能从环境变量或操作系统凭据存储读取。不要写入源码、清单、日志、测试夹具或打包参数。

```powershell
$env:WALLPAPER_API_KEY='your-local-key'
python main.py
```

`.env` 已被 Git 忽略，`.env.example` 只保留空变量名。项目当前不会自动读取 `.env`；如需该能力，可自行引入配置加载器。

## 数据映射

每条结果至少映射：`title`、`copyright`、`startdate`、`preview_url`、`full_url`。`startdate` 应是供应商范围内稳定且唯一的 ID。建议同时填写作者、许可证、许可证链接和原始来源页，以便用户核验版权。

## 接入规范

1. 使用 HTTPS，设置连接与读取超时，处理 `429` 和服务端错误。
2. 只接入明确允许下载、缓存和壁纸用途的内容。
3. 遵守署名、回链、下载追踪、缓存时限和速率限制。
4. 不记录 Token、完整本地路径、用户文件、设备标识或其他隐私数据。
5. 为字段映射、超时、空结果、限流和轮换顺序编写离线测试。
6. 不要在 UI 线程中执行网络请求。

接入前应重新阅读供应商最新 API 条款和每张图片的许可证。API 可访问不代表内容可自由再分发。
