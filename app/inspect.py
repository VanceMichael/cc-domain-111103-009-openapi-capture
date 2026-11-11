"""本地演示入口：解析描述、生成只读计划、比较版本差异并重放本地响应。

只使用 fixtures 下的合成资料，处理结果写入 runtime 目录，不访问外网。
"""
from app.diff import diff_plans, format_report
from app.openapi import operation_names, read_document
from app.plan import build_plan, load_config
from app.replay import ResponseBook, run_replay


def main():
    document = read_document("fixtures/v1.json")
    print("v1 操作:", operation_names(document))

    config_v1, base = load_config("fixtures/collection.v1.json")
    config_v2, _ = load_config("fixtures/collection.v2.json")
    plan_v1 = build_plan(config_v1, base_dir=base)
    plan_v2 = build_plan(config_v2, base_dir=base)
    for plan in (plan_v1, plan_v2):
        for spec in plan.request_specs():
            print(f"计划请求 [{plan.document_version}] {spec['method']} {spec['url']}")

    print()
    print(format_report(diff_plans(plan_v1, plan_v2)))
    print()

    for label, plan in (("v1", plan_v1), ("v2", plan_v2)):
        book = ResponseBook.from_file("fixtures/fixture-responses.json", label)
        for result in run_replay(plan, book, "runtime"):
            print(f"[{label}] {result['operation']} -> {result['status']} "
                  f"页数={len(result['pages'])} 记录={result['output_file']}")
            if result["quarantine"]:
                print("  隔离原因:", result["quarantine"]["reason"],
                      result["quarantine"]["detail"])


if __name__ == "__main__":
    main()
