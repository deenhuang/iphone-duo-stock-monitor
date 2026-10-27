import copy
import json
import io
import urllib.error
import unittest
from unittest.mock import patch

import monitor


PART = "MK2P4CH/A"


def payload(status="available", store="R471", **part_fields):
    return {"head": {"status": "200"}, "body": {"stores": [{
        "storeNumber": store, "storeName": "西湖", "city": "杭州",
        "retailStore": {"availableNow": True},
        "partsAvailability": {PART: {
            "pickupDisplay": status, "pickupSearchQuote": "测试取货状态",
            **part_fields,
        }},
    }]}}


def observation(available=True):
    return {"R471": {"available": available, "city": "杭州", "name": "西湖", "quote": "测试状态"}}


class StockTests(unittest.TestCase):
    def test_available_is_product_specific(self):
        self.assertTrue(monitor.parse_stores(payload(), PART)["R471"]["available"])
        self.assertFalse(monitor.parse_stores(payload("unavailable"), PART)["R471"]["available"])

    def test_unknown_or_missing_product_is_not_out_of_stock(self):
        for status in ["", None, "unknown", "ineligible", "notyetavailable"]:
            with self.subTest(status=status), self.assertRaises(monitor.MonitorError):
                monitor.parse_stores(payload(status), PART)
        data = payload()
        data["body"]["stores"][0]["partsAvailability"] = {}
        with self.assertRaises(monitor.MonitorError):
            monitor.parse_stores(data, PART)

    def test_empty_and_error_responses_fail(self):
        for data in [{}, {"head": {"status": "541"}, "body": {"stores": []}},
                     {"head": {"status": "200"}, "body": {"stores": []}},
                     {"head": {"status": "200"}, "body": {"stores": [None]}}]:
            with self.subTest(data=data), self.assertRaises(monitor.MonitorError):
                monitor.parse_stores(data, PART)

    def test_first_available_and_restock_notify_but_steady_stock_does_not(self):
        calls = []
        notify = lambda title, body: calls.append((title, body))
        state = monitor.advance({}, observation(), [], notify, 100)
        self.assertEqual(len(calls), 1)
        self.assertIn("西湖", calls[0][1])
        state = monitor.advance(state, observation(), [], notify, 200)
        self.assertEqual(len(calls), 1)
        state = monitor.advance(state, observation(False), [], notify, 300)
        state = monitor.advance(state, observation(), [], notify, 400)
        self.assertEqual(len(calls), 2)

    def test_failed_push_does_not_lose_restock(self):
        original = {"stores": {"R471": {"available": False}}}
        untouched = copy.deepcopy(original)
        with self.assertRaises(monitor.MonitorError):
            monitor.advance(original, observation(), [], lambda *args: (_ for _ in ()).throw(monitor.MonitorError("push failed")), 100)
        self.assertEqual(original, untouched)
        calls = []
        monitor.advance(original, observation(), [], lambda *args: calls.append(args), 200)
        self.assertEqual(len(calls), 1)

    def test_partial_failure_preserves_missing_store_but_notifies_valid_stock(self):
        state = {"stores": {"R390": {"available": True}}, "failures": 0}
        calls = []
        result = monitor.advance(state, observation(), ["上海查询失败"], lambda *args: calls.append(args), 100)
        self.assertTrue(result["stores"]["R390"]["available"])
        self.assertTrue(result["stores"]["R471"]["available"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["failures"], 1)

    def test_failures_do_not_create_false_restock_and_alert_after_three_runs(self):
        state = monitor.advance({}, observation(), [], lambda *args: None, 100)
        calls = []
        for now in [200, 300, 400, 500]:
            state = monitor.advance(state, {}, ["HTTP 541"], lambda *args: calls.append(args), now)
        self.assertEqual(len(calls), 1)
        self.assertIn("异常", calls[0][0])
        state = monitor.advance(state, observation(), [], lambda *args: calls.append(args), 600)
        self.assertEqual(len(calls), 2)
        self.assertIn("恢复", calls[-1][0])

    def test_collect_filters_nearby_cities_and_reports_missing_targets(self):
        config = {"part": PART, "locations": ["310000"], "stores": {"R471": {}, "R390": {}}}
        data = payload()
        data["body"]["stores"].append(payload(store="R688")["body"]["stores"][0])
        with patch.object(monitor, "request_json", return_value=data):
            observations, errors = monitor.collect(config)
        self.assertEqual(set(observations), {"R471"})
        self.assertIn("R390", " ".join(errors))

    def test_failed_location_can_be_covered_by_other_location(self):
        config = {"part": PART, "locations": ["310000", "200000"], "stores": {"R471": {}}}
        with patch.object(monitor, "request_json", side_effect=[monitor.MonitorError("541"), payload()]):
            observations, errors = monitor.collect(config)
        self.assertEqual(set(observations), {"R471"})
        self.assertEqual(errors, [])

    def test_malformed_nearby_store_does_not_hide_valid_target(self):
        config = {"part": PART, "locations": ["310000"], "stores": {"R471": {}}}
        data = payload()
        data["body"]["stores"].append(payload("unknown", "R688")["body"]["stores"][0])
        with patch.object(monitor, "request_json", return_value=data):
            observations, errors = monitor.collect(config)
        self.assertTrue(observations.get("R471", {}).get("available"))
        self.assertEqual(errors, [])

    def test_malformed_target_does_not_hide_other_valid_targets(self):
        config = {"part": PART, "locations": ["310000"], "stores": {"R471": {}, "R390": {}}}
        data = payload()
        data["body"]["stores"].append(payload("unknown", "R390")["body"]["stores"][0])
        with patch.object(monitor, "request_json", return_value=data):
            observations, errors = monitor.collect(config)
        self.assertTrue(observations.get("R471", {}).get("available"))
        self.assertIn("R390", " ".join(errors))

    def test_bark_server_error_fails_and_does_not_expose_key(self):
        with patch.object(monitor, "request_json", return_value={"code": 400}):
            with self.assertRaises(monitor.MonitorError) as error:
                monitor.Bark("private-key").send("title", "body")
        self.assertNotIn("private-key", str(error.exception))

    def test_bark_http_error_identifies_invalid_device_without_leaking_secret(self):
        response = io.BytesIO(json.dumps({"code": 400, "message": "failed to get device token: private-key"}).encode())
        failure = urllib.error.HTTPError("https://api.day.app/push", 400, "Bad Request", {}, response)
        with patch.object(monitor.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaises(monitor.MonitorError) as error:
                monitor.Bark("private-key").send("title", "body")
        self.assertIn("设备密钥", str(error.exception))
        self.assertNotIn("private-key", str(error.exception))

    def test_github_state_cannot_silently_reset_on_access_error(self):
        with patch.object(monitor, "request_json", side_effect=monitor.MonitorError("HTTP 403")):
            with self.assertRaises(monitor.MonitorError):
                monitor.GitHubState("owner/repo", "token").load()

    def test_state_schema_must_be_valid(self):
        for state in [[], {"stores": []}, {"stores": {"R471": {"available": "false"}}}]:
            with self.subTest(state=state), self.assertRaises(monitor.MonitorError):
                monitor.validate_state(state)


if __name__ == "__main__":
    unittest.main()
