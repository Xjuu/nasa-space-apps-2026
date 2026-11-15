import datetime as dt
import unittest

from backend import latency


def _csv(rows):
    head = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
    return (head + "\n".join(rows)).encode()


class LatencyTests(unittest.TestCase):
    def test_baseline_excluded_and_lag_measured(self):
        latency._state.update({"records": [], "seen": {}, "baseline_done": False, "polls": 0})
        row = lambda hm: f"10.0,20.0,330,0.4,0.4,2026-11-15,{hm},N,VIIRS,n,2.0NRT,290,5.0,D"
        t1 = dt.datetime(2026, 11, 15, 12, 0)
        names = list(latency._live.FEEDS)
        calls = {"n": 0}

        def fetch(url):
            rows = [row("1100"), row("1100").replace("10.0,20.0", "11.0,21.0")]      # >= 200 bytes so the feed is not treated as empty
            if calls["n"]:
                rows.append(row("1130"))
            return _csv(rows)
        latency.poll_once(fetch, t1)                       # baseline: nothing recorded
        self.assertEqual(latency._state["records"], [])
        calls["n"] = 1
        latency.poll_once(fetch, dt.datetime(2026, 11, 15, 12, 5))
        lags = [r[1] for r in latency._state["records"]]
        self.assertTrue(lags and all(abs(x - 35.0) < 0.2 for x in lags))   # 11:30 observed, first seen 12:05
        self.assertIn("median_min", latency.stats()["all"])


if __name__ == "__main__":
    unittest.main()
