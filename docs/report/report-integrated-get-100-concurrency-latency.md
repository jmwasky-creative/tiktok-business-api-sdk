# report_integrated_get 100 并发限流延时报告

| 项 | 值 |
| --- | --- |
| 日期 | 2026-06-21 |
| SDK | Python SDK |
| 接口 | `ReportingApi.report_integrated_get` |
| 请求路径 | `GET /open_api/v1.3/report/integrated/get/` |
| 测试用例 | `test_report_integrated_get_one_hundred_concurrent_requests_delay` |
| 并发数 | 100 |
| 配置 QPS | 20 |
| 单请求最小间隔 | 0.05 秒 |
| HTTP 层 | Fake REST client，无真实外网请求 |

## 结论

在默认 `qps=20` 的 SDK 进程级限流下，100 条同时发起的 `report_integrated_get` 请求会被按 0.05 秒间隔排队发送：

| 指标 | 延时 |
| --- | ---: |
| 第 1 条请求 | 0.00 秒 |
| 第 2 条请求 | 0.05 秒 |
| p95 等待时间 | 4.70 秒 |
| p99 等待时间 | 4.90 秒 |
| 最大等待时间 | 4.95 秒 |
| 平均等待时间 | 2.475 秒 |
| 累计等待时间 | 247.5 秒 |

因此，如果只看这一层 SDK 限流，100 条同时请求 `getreport` 的尾部排队延时约为 **4.95 秒**。这个结果不包含 TikTok Business API 服务端处理时间、网络耗时、DNS/TLS 建连耗时，也不包含调用方业务逻辑耗时。

## 测试方法

新增单测使用 100 个线程和 `threading.Barrier`，确保所有线程同时开始调用 `ReportingApi.report_integrated_get`。HTTP 请求层使用 fake `GET` 响应，避免真实访问外网；`time.monotonic` 固定为 `0.0`，`time.sleep` 被替换为记录等待秒数的 fake 函数，因此测试不会真实等待 4.95 秒，但会验证限流器为 100 条并发请求排出的计划等待时间。

关键断言：

| 断言 | 期望 |
| --- | --- |
| 成功请求数 | 100 |
| sleep 次数 | 99 |
| 最小 sleep | 0.05 秒 |
| 最大 sleep | 4.95 秒 |
| p95 等待 | 4.70 秒 |
| p99 等待 | 4.90 秒 |
| 累计 sleep | 247.5 秒 |
| 平均等待 | 2.475 秒 |

## 验证命令

```bash
cd /Users/a1/workplace/codex/tk-business-sdk/python_sdk
python3 -m unittest tests.test_rate_limit -v
```

运行结果：

```text
Ran 12 tests in 0.140s

OK
```
