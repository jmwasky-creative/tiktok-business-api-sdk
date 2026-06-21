# coding: utf-8

from __future__ import absolute_import

import io
import importlib.util
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
PACKAGE_DIR = os.path.join(ROOT_DIR, "business_api_client")


def _ensure_package(name, path):
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = [path]
        sys.modules[name] = module
    return module


def _ensure_test_stubs():
    _ensure_package("business_api_client", PACKAGE_DIR)
    _ensure_package("business_api_client.api", os.path.join(PACKAGE_DIR, "api"))
    _ensure_package("business_api_client.models", os.path.join(PACKAGE_DIR, "models"))
    _ensure_package("business_api_client.tiktok_business", os.path.join(PACKAGE_DIR, "tiktok_business"))

    rest_module = sys.modules.get("business_api_client.rest")
    if rest_module is None:
        rest_module = types.ModuleType("business_api_client.rest")

        class RESTClientObject(object):
            pass

        rest_module.RESTClientObject = RESTClientObject
        sys.modules["business_api_client.rest"] = rest_module

    exceptions_module = sys.modules.get("business_api_client.tiktok_business.tiktok_exceptions")
    if exceptions_module is None:
        exceptions_module = types.ModuleType("business_api_client.tiktok_business.tiktok_exceptions")

        class TiktokSDKError(Exception):
            pass

        exceptions_module.TiktokSDKError = TiktokSDKError
        sys.modules["business_api_client.tiktok_business.tiktok_exceptions"] = exceptions_module

    code_module = sys.modules.get("business_api_client.tiktok_business.tiktok_code")
    if code_module is None:
        code_module = types.ModuleType("business_api_client.tiktok_business.tiktok_code")

        class NumericErrorCodes(object):
            ERROR_CODE_OK = 0

        code_module.NumericErrorCodes = NumericErrorCodes
        sys.modules["business_api_client.tiktok_business.tiktok_code"] = code_module

    response_module = sys.modules.get("business_api_client.tiktok_business.tiktok_response")
    if response_module is None:
        response_module = types.ModuleType("business_api_client.tiktok_business.tiktok_response")

        class TikTokSDKResponse(object):
            def __init__(self, data=None, request_id=None, code=None, message=""):
                self.data = data
                self.request_id = request_id
                self.code = code
                self.message = message

            def response(self):
                return {
                    "data": self.data,
                    "request_id": self.request_id,
                }

        response_module.TikTokSDKResponse = TikTokSDKResponse
        sys.modules["business_api_client.tiktok_business.tiktok_response"] = response_module


def _load_module(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class RateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_test_stubs()
        cls.configuration_module = _load_module(
            "business_api_client.configuration",
            os.path.join(PACKAGE_DIR, "configuration.py"),
        )
        cls.api_client_module = _load_module(
            "business_api_client.api_client",
            os.path.join(PACKAGE_DIR, "api_client.py"),
        )
        cls.reporting_api_module = _load_module(
            "business_api_client.api.reporting_api",
            os.path.join(PACKAGE_DIR, "api", "reporting_api.py"),
        )

    def setUp(self):
        self.api_client_module._GLOBAL_RATE_LIMITER.reset()

    def test_configuration_reads_default_qps_from_config_file(self):
        configuration = self.configuration_module.Configuration()
        self.assertEqual(configuration.qps, 20.0)

    def test_throttle_request_is_thread_safe_and_respects_qps(self):
        client = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client.configuration = types.SimpleNamespace(qps=20)

        clock = [0.0]
        sleeps = []

        def fake_monotonic():
            return clock[0]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        barrier = threading.Barrier(2)
        errors = []

        def worker():
            try:
                barrier.wait()
                client._throttle_request()
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)

        with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
             mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
            first.start()
            second.start()
            first.join(timeout=5)
            second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.05)
        self.assertAlmostEqual(self.api_client_module._GLOBAL_RATE_LIMITER._next_request_at, 0.1)

    def test_throttle_request_is_shared_across_client_instances(self):
        client_a = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client_a.configuration = types.SimpleNamespace(qps=20)
        client_b = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client_b.configuration = types.SimpleNamespace(qps=20)

        clock = [0.0]
        sleeps = []

        def fake_monotonic():
            return clock[0]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
             mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
            client_a._throttle_request()
            client_b._throttle_request()

        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.05)
        self.assertAlmostEqual(self.api_client_module._GLOBAL_RATE_LIMITER._next_request_at, 0.1)

    def test_rate_limit_logs_are_debug_only(self):
        client_a = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client_a.configuration = types.SimpleNamespace(qps=20)
        client_b = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client_b.configuration = types.SimpleNamespace(qps=20)

        clock = [0.0]
        sleeps = []
        log_stream = io.StringIO()
        handler = self.api_client_module.logging.StreamHandler(log_stream)
        handler.setLevel(self.api_client_module.logging.DEBUG)
        logger = self.api_client_module.logger
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.addHandler(handler)
        logger.setLevel(self.api_client_module.logging.DEBUG)
        logger.propagate = False

        def fake_monotonic():
            return clock[0]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        try:
            with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
                 mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
                client_a._throttle_request()
                client_b._throttle_request()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        log_output = log_stream.getvalue()

        self.assertEqual(len(sleeps), 1)
        self.assertIn("[rate_limit] wait=0.000000 qps=20.00", log_output)
        self.assertIn("[rate_limit] wait=0.050000 qps=20.00", log_output)
        self.assertIn("[rate_limit] scheduled_next=0.100000 qps=20.00", log_output)

    def test_rate_limit_does_not_emit_info_logs_by_default(self):
        client_a = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client_a.configuration = types.SimpleNamespace(qps=20)
        client_b = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client_b.configuration = types.SimpleNamespace(qps=20)

        log_stream = io.StringIO()
        handler = self.api_client_module.logging.StreamHandler(log_stream)
        handler.setLevel(self.api_client_module.logging.INFO)
        logger = self.api_client_module.logger
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.addHandler(handler)
        logger.setLevel(self.api_client_module.logging.INFO)
        logger.propagate = False

        clock = [0.0]

        def fake_monotonic():
            return clock[0]

        def fake_sleep(seconds):
            clock[0] += seconds

        try:
            with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
                 mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
                client_a._throttle_request()
                client_b._throttle_request()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        self.assertEqual(log_stream.getvalue(), "")

    def test_throttle_sleeps_outside_limiter_lock(self):
        limiter = self.api_client_module._GLOBAL_RATE_LIMITER
        lock_available_during_sleep = []

        def fake_monotonic():
            return 0.0

        def fake_sleep(seconds):
            acquired = limiter._lock.acquire(False)
            lock_available_during_sleep.append(acquired)
            if acquired:
                limiter._lock.release()

        with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
             mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
            limiter.throttle(20)
            limiter.throttle(20)

        self.assertEqual(lock_available_during_sleep, [True])

    def test_concurrent_throttle_reserves_global_schedule(self):
        limiter = self.api_client_module._GLOBAL_RATE_LIMITER
        worker_count = 8
        calls_per_worker = 5
        barrier = threading.Barrier(worker_count)
        sleeps = []
        errors = []

        def fake_monotonic():
            return 0.0

        def fake_sleep(seconds):
            sleeps.append(seconds)

        def worker():
            try:
                barrier.wait()
                for _ in range(calls_per_worker):
                    limiter.throttle(20)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(worker_count)]

        with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
             mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(sleeps), worker_count * calls_per_worker - 1)
        self.assertAlmostEqual(limiter._next_request_at, 2.0)
        for index, wait_seconds in enumerate(sorted(sleeps), start=1):
            self.assertAlmostEqual(wait_seconds, index * 0.05)

    def test_async_req_returns_before_rate_limiter_releases(self):
        client = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client.configuration = types.SimpleNamespace(
            qps=20,
            host="https://business-api.tiktok.com",
            safe_chars_for_path_param="",
        )
        client.default_headers = {"Business-SDK": 1, "SDK-Language": "Py", "SDK-Version": "0.1.3"}
        client.cookie = None
        client.pool = self.api_client_module.ThreadPool(1)

        class FakeResponse(object):
            code = 0
            message = ""
            request_id = "async-req"
            data = {"request_id": request_id}

        client.rest_client = types.SimpleNamespace(GET=lambda url, **kwargs: FakeResponse())
        throttle_started = threading.Event()
        throttle_released = threading.Event()

        def blocking_throttle(qps):
            throttle_started.set()
            throttle_released.wait(0.5)

        start = time.time()
        try:
            with mock.patch.object(
                    self.api_client_module._GLOBAL_RATE_LIMITER,
                    "throttle",
                    side_effect=blocking_throttle):
                result = client.call_api(
                    "/async/test/",
                    "GET",
                    async_req=True,
                    _return_http_data_only=True,
                    _preload_content=False,
                )
                elapsed = time.time() - start
                self.assertLess(elapsed, 0.2)
                self.assertTrue(throttle_started.wait(1))
                self.assertFalse(result.ready())
                throttle_released.set()
                response = result.get(timeout=5)
        finally:
            throttle_released.set()
            client.pool.close()
            client.pool.join()

        self.assertEqual(response.request_id, "async-req")

    def test_report_integrated_get_builds_expected_request(self):
        captured = {}

        class FakeApiClient(object):
            def select_header_accept(self, accepts):
                return "application/json"

            def call_api(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                return {"result": "ok"}

        api = self.reporting_api_module.ReportingApi(api_client=FakeApiClient())
        result = api.report_integrated_get(
            "BASIC",
            "access-token-example",
            page=2,
            page_size=50,
            advertiser_id="123456",
            dimensions=["stat_time_day"],
            metrics=["spend"],
        )

        self.assertEqual(result, {"result": "ok"})
        self.assertIn("args", captured)
        self.assertIn("kwargs", captured)

        path, method, path_params, query_params, header_params = captured["args"][:5]
        self.assertEqual(path, "/open_api/v1.3/report/integrated/get/")
        self.assertEqual(method, "GET")
        self.assertEqual(path_params, {})
        self.assertIn(("report_type", "BASIC"), query_params)
        self.assertIn(("page", 2), query_params)
        self.assertIn(("page_size", 50), query_params)
        self.assertIn(("advertiser_id", "123456"), query_params)
        self.assertIn(("dimensions", ["stat_time_day"]), query_params)
        self.assertIn(("metrics", ["spend"]), query_params)
        self.assertEqual(header_params["Access-Token"], "access-token-example")

    def test_report_integrated_get_limits_more_than_twenty_requests(self):
        configuration = self.configuration_module.Configuration()
        configuration.qps = 20
        client = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client.configuration = configuration
        client.default_headers = {"Business-SDK": 1, "SDK-Language": "Py", "SDK-Version": "0.1.3"}
        client.cookie = None
        client.rest_client = None

        calls = []

        class FakeResponse(object):
            def __init__(self, request_id):
                self.code = 0
                self.message = ""
                self.request_id = request_id
                self.data = {"request_id": request_id}

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse("req-%d" % len(calls))

        client.rest_client = types.SimpleNamespace(GET=fake_get)
        api = self.reporting_api_module.ReportingApi(api_client=client)

        clock = [0.0]
        sleeps = []

        def fake_monotonic():
            return clock[0]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        results = []
        with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
             mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
            for _ in range(21):
                results.append(
                    api.report_integrated_get(
                        "BASIC",
                        "access-token-example",
                        advertiser_id="123456",
                        dimensions=["stat_time_day"],
                        metrics=["spend"],
                        _preload_content=False,
                    )
                )

        self.assertEqual(len(calls), 21)
        self.assertEqual(len(sleeps), 20)
        self.assertAlmostEqual(sum(sleeps), 1.0)
        self.assertEqual(results[-1]["request_id"], "req-21")

    def test_report_integrated_get_limits_forty_requests(self):
        configuration = self.configuration_module.Configuration()
        configuration.qps = 20
        client = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client.configuration = configuration
        client.default_headers = {"Business-SDK": 1, "SDK-Language": "Py", "SDK-Version": "0.1.3"}
        client.cookie = None
        client.rest_client = None

        calls = []

        class FakeResponse(object):
            def __init__(self, request_id):
                self.code = 0
                self.message = ""
                self.request_id = request_id
                self.data = {"request_id": request_id}

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse("req-%d" % len(calls))

        client.rest_client = types.SimpleNamespace(GET=fake_get)
        api = self.reporting_api_module.ReportingApi(api_client=client)

        clock = [0.0]
        sleeps = []

        def fake_monotonic():
            return clock[0]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        results = []
        with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
             mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
            for _ in range(40):
                results.append(
                    api.report_integrated_get(
                        "BASIC",
                        "access-token-example",
                        advertiser_id="123456",
                        dimensions=["stat_time_day"],
                        metrics=["spend"],
                        _preload_content=False,
                    )
                )

        self.assertEqual(len(calls), 40)
        self.assertEqual(len(sleeps), 39)
        self.assertAlmostEqual(sum(sleeps), 1.95)
        self.assertEqual(results[-1]["request_id"], "req-40")

    def test_report_integrated_get_one_hundred_concurrent_requests_delay(self):
        configuration = self.configuration_module.Configuration()
        configuration.qps = 20
        client = self.api_client_module.ApiClient.__new__(self.api_client_module.ApiClient)
        client.configuration = configuration
        client.default_headers = {"Business-SDK": 1, "SDK-Language": "Py", "SDK-Version": "0.1.3"}
        client.cookie = None
        client.rest_client = None

        call_lock = threading.Lock()
        calls = []

        class FakeResponse(object):
            def __init__(self, request_id):
                self.code = 0
                self.message = ""
                self.request_id = request_id
                self.data = {"request_id": request_id}

        def fake_get(url, **kwargs):
            with call_lock:
                calls.append((url, kwargs))
                request_id = "req-%d" % len(calls)
            return FakeResponse(request_id)

        client.rest_client = types.SimpleNamespace(GET=fake_get)
        api = self.reporting_api_module.ReportingApi(api_client=client)

        worker_count = 100
        barrier = threading.Barrier(worker_count)
        sleeps = []
        sleep_lock = threading.Lock()
        results = []
        result_lock = threading.Lock()
        errors = []
        error_lock = threading.Lock()

        def fake_monotonic():
            return 0.0

        def fake_sleep(seconds):
            with sleep_lock:
                sleeps.append(seconds)

        def worker():
            try:
                barrier.wait()
                result = api.report_integrated_get(
                    "BASIC",
                    "access-token-example",
                    advertiser_id="123456",
                    dimensions=["stat_time_day"],
                    metrics=["spend"],
                    _preload_content=False,
                )
                with result_lock:
                    results.append(result)
            except Exception as exc:
                with error_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(worker_count)]

        with mock.patch.object(self.api_client_module.time, "monotonic", side_effect=fake_monotonic), \
             mock.patch.object(self.api_client_module.time, "sleep", side_effect=fake_sleep):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(calls), worker_count)
        self.assertEqual(len(results), worker_count)
        self.assertEqual(len(sleeps), worker_count - 1)

        sorted_sleeps = sorted(sleeps)
        scheduled_waits = [0.0] + sorted_sleeps
        self.assertAlmostEqual(sorted_sleeps[0], 0.05)
        self.assertAlmostEqual(sorted_sleeps[-1], 4.95)
        self.assertAlmostEqual(scheduled_waits[94], 4.70)
        self.assertAlmostEqual(scheduled_waits[98], 4.90)
        self.assertAlmostEqual(sum(sleeps), 247.5)
        self.assertAlmostEqual(sum(sleeps) / worker_count, 2.475)
        self.assertEqual(
            sorted(int(result["request_id"].split("-")[1]) for result in results),
            list(range(1, worker_count + 1)),
        )


if __name__ == "__main__":
    unittest.main()
