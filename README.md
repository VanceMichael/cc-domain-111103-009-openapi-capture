# 版本化 OpenAPI 描述驱动的只读采集

这是一个 Python 数据采集项目。当前代码提供来源材料的基础读取入口，合成输入保存在 fixtures，已有读取行为由 tests 验证。

## 来源材料

v1.json 与 v2.json 是合成 OpenAPI 文档，分页游标分别采用字符串和对象。fixture-responses.json 保存本地分页响应，其中包括跨 origin 的 next 链接。当前读取入口检查文档结构并枚举操作，不发送请求。

上面的样例用于说明来源字段和当前读取边界。项目按原始来源保存材料；同一来源的版本变化和不同来源的同名记录需要分别核对。示例文件只用于本地调试，不代表真实企业、机构或设备数据。

## 本地运行

项目使用 Python 3.12 或更新版本，仅依赖标准库。从项目根目录执行：

```sh
python3 -m app.inspect
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests
```

示例只使用本地虚构资料。处理结果放在 runtime 目录，源文件应保持只读。若连接真实来源，先按来源方授权范围配置读取权限。
