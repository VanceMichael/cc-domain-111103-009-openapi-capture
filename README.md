# 版本化 OpenAPI 描述驱动的只读采集

这是一个 Python 数据采集项目。项目从 OpenAPI 描述生成受控的只读读取计划，在描述变化时输出可审阅的运行含义差异，并通过本地响应重放验证采集行为。合成输入保存在 fixtures，已有读取行为由 tests 验证。

## 来源材料

v1.json 与 v2.json 是合成 OpenAPI 文档，分页游标分别采用字符串和对象。loop.json 与 common.json 覆盖循环引用与跨文件 `$ref`，并声明多种数组查询参数格式；drift-v1.json 与 drift-v2.json 覆盖单字段类型漂移与枚举新增。collection.v1.json 与 collection.v2.json 是采集配置，各自引用文档版本、允许访问的操作、字段映射与映射版本。fixture-responses.json 保存本地分页响应，其中包括跨 origin 的 next 链接和违背 schema 的页。

上面的样例用于说明来源字段和当前读取边界。项目按原始来源保存材料；同一来源的版本变化和不同来源的同名记录需要分别核对。示例文件只用于本地调试，不代表真实企业、机构或设备数据。

## 模块

- `app/openapi.py`：文档读取与操作枚举。
- `app/refs.py`：`$ref` 解析，支持跨文件引用与循环检测；远程引用一律拒绝。
- `app/plan.py`：从采集配置生成只读计划。只允许 GET；路径级参数按继承合并；path/operation 级 servers 覆盖会被拒绝；server+path 拼接必须留在声明的 https 来源内。
- `app/serialize.py`：按声明的 style/explode 序列化查询与路径参数；校验分页链接的 origin 与路径范围。
- `app/schema.py`：响应 schema 校验（JSON Schema 子集），循环引用下保证终止。
- `app/diff.py`：比较两份描述，输出类型变宽、枚举新增、分页结构变化等差异及受影响映射。
- `app/replay.py`：本地响应重放。响应违背 schema 或分页链接越界时隔离对应操作：保留已采集页，不伪装成空页，也不覆盖已完成的批次文件。每条采集结果记录所用描述摘要（sha256）与映射版本。

## 升级流程

1. 用 `app.diff.diff_plans` 比较新旧计划对应的描述，审阅差异与受影响映射。
2. 操作者确认后新增采集配置（如 collection.v2.json），旧配置保留。
3. 旧批次按旧配置重放结果不变；新旧描述可同时存在。

## 本地运行

项目使用 Python 3.12 或更新版本，仅依赖标准库。从项目根目录执行：

```sh
python3 -m app.inspect
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests
```

示例只使用本地虚构资料，全程不访问外网、不发送写请求。处理结果放在 runtime 目录，源文件应保持只读。若连接真实来源，先按来源方授权范围配置读取权限。
