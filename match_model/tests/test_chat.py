"""Offline tests for tennis/chat.py: the tools, the schemas, the key loading,
the chat loop (with a fake client) and the local page. No API calls."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tennis import chat, patterns as pt

ROOT = Path(__file__).resolve().parent.parent
OUT1 = ROOT / "out" / "ABN_AMRO_2023_Halys_vs_Barrere"

FAKE_KEY = "sk-test-not-a-real-key-123456"


# ---------------------------------------------------------------------------
# synthetic matches
# ---------------------------------------------------------------------------
def _point(pid, server, label, winner, serves, side=None, review=None, in_tb=False, set_no=1, flags=None,
           ending=None):
    a, b = (label.split("-") if not in_tb else label.split("-"))
    if not in_tb:
        n = chat._label_counts(label)
        side = side or ("deuce" if sum(n) % 2 == 0 else "ad")
    p = {"id": pid, "t_start": 10.0 * pid, "t_end": 10.0 * pid + 8, "set_no": set_no,
         "score_before": {"sets": [], "games": [0, 0], "points": label if server == "A" else "%s-%s" % (b, a),
                          "in_tb": in_tb},
         "server": server, "receiver": "B" if server == "A" else "A", "side": side or "deuce",
         "winner": winner, "serves": serves, "ending": ending,
         "serve_number_won_on": next((s["n"] for s in serves if s.get("result") == "in"), None)}
    if flags is not None:
        p["flags"] = flags
    if review is not None:
        p["review"] = review
    return p


def _serve(n, result, zone=None):
    return {"n": n, "t": 1.0, "result": result, "zone": zone, "fault": None, "bounce_xy": None,
            "speed_kmh": None, "conf": 0.5}


CHECK_ALL = {"status": "reviewed"}


def _write(tmp, points, fmt=None):
    m = {"meta": {"players": {"A": {"name": "Ann"}, "B": {"name": "Bea"}}, "format": fmt or {}},
         "points": points}
    d = Path(tmp)
    (d / "match.json").write_text(json.dumps(m), encoding="utf-8")
    return chat.MatchData(d)


class TmpMatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def data(self, points, fmt=None):
        return _write(self._tmp.name, points, fmt)


# ---------------------------------------------------------------------------
# side rule
# ---------------------------------------------------------------------------
class TestSideRule(unittest.TestCase):
    def test_scores_fix_the_side(self):
        for lab, side in (("0-0", "deuce"), ("15-0", "ad"), ("40-0", "ad"), ("0-40", "ad"), ("30-30", "deuce"),
                          ("40-40", "deuce"), ("AD-40", "ad"), ("40-AD", "ad"), ("30-40", "ad"), ("15-30", "ad"),
                          ("40-15", "deuce"), ("15-15", "deuce")):
            r = chat.side_rule("all", lab)
            self.assertEqual(r["possible_sides"], [side], lab)

    def test_situations_fix_the_side(self):
        self.assertEqual(chat.side_rule("40-0", "any")["possible_sides"], ["ad"])
        self.assertEqual(chat.side_rule("deuce", "any")["possible_sides"], ["deuce"])
        self.assertEqual(chat.side_rule("first_point", "any")["possible_sides"], ["deuce"])
        for sit in ("all", "break_point", "game_point", "tiebreak", "set_point", "match_point"):
            self.assertEqual(chat.side_rule(sit, "any")["possible_sides"], ["deuce", "ad"], sit)

    def test_40_0_deuce_note(self):
        note = chat.side_rule("40-0", "any")["note"]
        self.assertIn("3 points", note)
        self.assertIn("cannot happen", note)
        self.assertIn("ad side", note)
        self.assertNotIn("\u2014", note)

    def test_no_ad_deciding_point(self):
        self.assertEqual(chat.side_rule("deuce", "any", no_ad=True)["possible_sides"], ["deuce", "ad"])

    def test_conflicts(self):
        self.assertEqual(chat.side_rule("40-0", "30-30")["possible_sides"], [])
        self.assertEqual(chat.side_rule("tiebreak", "40-0")["possible_sides"], [])
        self.assertEqual(chat.side_rule("40-0", "40-0")["possible_sides"], ["ad"])


# ---------------------------------------------------------------------------
# tools on synthetic data
# ---------------------------------------------------------------------------
class TestTools(TmpMatch):
    def test_sources_kept_apart(self):
        pts = [_point(1, "A", "0-0", "A", [_serve(1, "in", "T")], review=CHECK_ALL),
               _point(2, "A", "15-0", "A", [_serve(1, "in", "wide")]),
               _point(3, "A", "30-0", "B", [_serve(1, "fault"), _serve(2, "in", "body")],
                      review={"status": "edited", "checked_fields": ["serve1_result"]})]
        d = self.data(pts)
        r = d.serve_directions("A", "all", "any", "all", "both")
        ck, au = r["sources"]["eye_checked"], r["sources"]["automatic"]
        self.assertEqual(ck["direction_counts"], {"T": 1, "body": 0, "wide": 0})
        self.assertEqual(ck["faults"], 1)
        self.assertEqual(au["direction_counts"], {"T": 0, "body": 1, "wide": 1})
        self.assertEqual(ck["serves"] + au["serves"], 4)

    def test_too_small_and_shares(self):
        pts = [_point(i, "A", "0-0", "A", [_serve(1, "in", "wide" if i % 3 else "T")], review=CHECK_ALL)
               for i in range(1, 10)]
        d = self.data(pts)
        g = d.serve_directions("A", "all", "any", "1", "deuce")["sources"]["eye_checked"]
        self.assertEqual(g["direction_seen"], 9)
        self.assertTrue(g["too_small"])
        self.assertIn("too few", g["habit_rule"])
        self.assertEqual(g["direction_shares"]["wide"], "6 of 9 (67%)")
        pts.append(_point(10, "A", "0-0", "B", [_serve(1, "in", "body")], review=CHECK_ALL))
        g = self.data(pts).serve_directions("A", "all", "any", "1", "deuce")["sources"]["eye_checked"]
        self.assertEqual(g["direction_seen"], 10)
        self.assertFalse(g["too_small"])
        self.assertEqual(g["most_often"], "wide")
        self.assertEqual(g["points_won_by_server_after_this_direction"]["body"],
                         {"share": "0 of 1 (0%)", "too_small": True})

    def test_points_won_flag_at_ten(self):
        pts = [_point(i, "A", "0-0", "A", [_serve(1, "in", "wide")], review=CHECK_ALL) for i in range(1, 11)]
        g = self.data(pts).serve_directions("A", "all", "any", "1", "both")["sources"]["eye_checked"]
        self.assertEqual(g["points_won_by_server_after_this_direction"]["wide"],
                         {"share": "10 of 10 (100%)", "too_small": False})

    def test_comparison_block_only_for_a_situation(self):
        pts = [_point(1, "A", "40-0", "A", [_serve(1, "in", "T")], review=CHECK_ALL),
               _point(2, "A", "15-0", "A", [_serve(1, "fault"), _serve(2, "in", "body")], review=CHECK_ALL)]
        d = self.data(pts)
        g = d.serve_directions("A", "40-0", "any", "all", "ad")["sources"]["eye_checked"]
        self.assertEqual(g["same_serves_all_points_for_comparison"]["went_in_share"], "2 of 3 (67%)")
        g = d.serve_directions("A", "all", "any", "all", "ad")["sources"]["eye_checked"]
        self.assertNotIn("same_serves_all_points_for_comparison", g)

    def test_impossible_side_answer_and_data_problem(self):
        pts = [_point(1, "A", "40-0", "A", [_serve(1, "in", "T")], review=CHECK_ALL)]
        r = self.data(pts).serve_directions("A", "40-0", "any", "all", "deuce")
        self.assertFalse(r["side_rule"]["asked_side_possible"])
        self.assertIsNone(r["sources"])
        self.assertNotIn("data_problem", r["side_rule"])
        bad = [_point(1, "A", "40-0", "A", [_serve(1, "in", "T")], side="deuce", review=CHECK_ALL)]
        r = self.data(bad).serve_directions("A", "40-0", "any", "all", "deuce")
        self.assertIn("data_problem", r["side_rule"])

    def test_double_faults_and_score_disagrees(self):
        pts = [_point(1, "A", "0-0", "B", [_serve(1, "fault"), _serve(2, "fault")], review=CHECK_ALL),
               _point(2, "A", "0-15", "A", [_serve(1, "fault"), _serve(2, "fault")], review=CHECK_ALL)]
        s = self.data(pts).serve_summary("A")["sources"]["eye_checked"]
        self.assertEqual(s["double_faults"], 1)
        self.assertEqual(s["second_serve_faults_on_points_the_server_won"], 1)
        self.assertEqual(s["first_serve_in"], "0 of 2 (0%)")

    def test_list_points_and_winner_sources(self):
        bp = {"break_point": True, "game_point": False, "set_point": False, "match_point": False}
        pts = [_point(1, "A", "30-40", "A", [_serve(1, "in", "T")], review=CHECK_ALL, flags=bp),
               _point(2, "A", "30-40", "B", [_serve(1, "in", "wide")], flags=bp),
               _point(3, "A", "0-0", "A", [_serve(1, "in", "T")], review=CHECK_ALL)]
        r = self.data(pts).list_points("A", "break_point", "any", "both", "any", "any", 0, 0)
        self.assertEqual(r["points_matching"], 2)
        self.assertEqual(r["server_won"], {"eye_checked": "1 of 1 (100%)", "automatic": "0 of 1 (0%)"})
        self.assertEqual([x["point"] for x in r["points"]], [1, 2])
        self.assertEqual(r["points"][0]["winner"], "Ann")
        r = self.data(pts).list_points("any", "all", "30-40", "ad", "B", "any", 0, 1)
        self.assertEqual(r["points_matching"], 1)
        r = self.data(pts).list_points("any", "all", "any", "both", "any", "any", 0, 1)
        self.assertEqual(r["points_not_shown"], 2)

    def test_point_detail(self):
        pts = [_point(1, "A", "0-0", "A", [_serve(1, "in", "T")],
                      review={"status": "edited", "checked_fields": ["winner", "serve1_result"]})]
        d = self.data(pts)
        r = d.point_detail(1)
        self.assertEqual(r["winner"], {"value": "Ann", "source": "eye_checked"})
        self.assertEqual(r["serves"][0]["result"]["source"], "eye_checked")
        self.assertEqual(r["serves"][0]["direction"], {"value": "T", "source": "automatic"})
        self.assertEqual(r["serves"][0]["counted_as"], "automatic")
        self.assertEqual(r["how_it_ended"]["source"], "not known")
        self.assertIn("error", d.point_detail(99))

    def test_tiebreak_score_server_first(self):
        p = _point(1, "B", "0-0", "B", [_serve(1, "in", "T")], review=CHECK_ALL)
        p["score_before"] = {"sets": [], "games": [6, 6], "points": "1-6", "in_tb": True}
        d = self.data([p])
        self.assertEqual(d.server_score(d.points[0]), "tiebreak 6-1")

    def test_overview_result(self):
        p = _point(1, "A", "40-0", "A", [_serve(1, "in", "T")], review=CHECK_ALL)
        p["score_before"] = {"sets": [[6, 0]], "games": [5, 0], "points": "40-0", "in_tb": False}
        ov = self.data([p], fmt={"best_of": 3}).overview()
        self.assertTrue(ov["result"]["known"])
        self.assertEqual(ov["result"]["winner"], "Ann")
        self.assertEqual(ov["result"]["sets"], ["set 1: Ann 6, Bea 0", "set 2: Ann 6, Bea 0"])
        q = _point(1, "A", "0-0", "A", [_serve(1, "in", "T")])
        ov = self.data([q]).overview()
        self.assertFalse(ov["result"]["known"])
        self.assertEqual(ov["points"]["winner_not_eye_checked"], [1])
        self.assertIn("eye_check", ov)

    def test_overview_says_where_the_score_comes_from(self):
        p = _point(1, "A", "0-0", "A", [_serve(1, "in", "T")])
        d = self.data([p])
        self.assertIsNone(d.overview()["score_source"]["score_read_from_board"])
        for hb, word in ((True, "score board"), (False, "guessed")):
            d.meta["has_scorebug"] = hb
            d.meta["scorebug_warning"] = None if hb else "No score board was found in the video."
            ss = d.overview()["score_source"]
            self.assertIs(ss["score_read_from_board"], hb)
            self.assertIn(word, ss["how"])
            self.assertEqual("warning" in ss, not hb)
        self.assertIn("score_source", chat.SYSTEM_PROMPT)

    def test_points_per_side_cover_every_side(self):
        # gap close step: asking for the ad side once gave {"deuce": 0, "ad": 2}, which the bot read as
        # "none from the deuce side" while the player had a break point on the deuce side too
        bp = {"break_point": True}
        pts = [_point(1, "A", "30-40", "B", [_serve(1, "in", "T")], review=CHECK_ALL, flags=bp),
               _point(2, "A", "40-AD", "A", [_serve(1, "in", "wide")], review=CHECK_ALL, flags=bp),
               _point(3, "A", "15-40", "A", [_serve(1, "in", "body")], review=CHECK_ALL, flags=bp),
               _point(4, "A", "0-0", "A", [_serve(1, "in", "T")], review=CHECK_ALL)]
        d = self.data(pts)
        for side, mine in (("ad", 2), ("deuce", 1), ("side_unknown", 0)):
            r = d.serve_directions("A", "break_point", "any", "1", side)
            self.assertEqual(r["points_served_in_group"], {"deuce": 1, "ad": 2, "side_unknown": 0, "total": 3}, side)
            self.assertEqual(r["points_served_on_asked_side"], mine, side)
            self.assertIn("every side", r["points_note"])
        ad = d.serve_directions("A", "break_point", "any", "1", "ad")["sources"]["eye_checked"]
        self.assertEqual(ad["direction_counts"], {"T": 1, "body": 0, "wide": 1})
        both = d.serve_directions("A", "break_point", "any", "1", "both")
        self.assertEqual(both["points_served_in_group"]["total"], 3)
        self.assertNotIn("points_served_on_asked_side", both)
        self.assertNotIn("points_note", both)
        # the impossible-side check still counts the asked side's points
        p5 = _point(5, "A", "40-0", "A", [_serve(1, "in", "T")], side="deuce", review=CHECK_ALL)
        r = self.data(pts + [p5]).serve_directions("A", "40-0", "any", "all", "deuce")
        self.assertFalse(r["side_rule"]["asked_side_possible"])
        self.assertIn("1 point in this impossible group", r["side_rule"]["data_problem"])


# ---------------------------------------------------------------------------
# schemas and input checks
# ---------------------------------------------------------------------------
class TestSchemas(unittest.TestCase):
    def test_strict_schemas(self):
        names = set()
        for t in chat.TOOLS:
            names.add(t["name"])
            self.assertTrue(t["strict"])
            self.assertTrue(t["eager_input_streaming"])
            s = t["input_schema"]
            self.assertEqual(s["type"], "object")
            self.assertIs(s["additionalProperties"], False)
            self.assertEqual(sorted(s["required"]), sorted(s["properties"]))
            for k, v in s["properties"].items():
                self.assertIn(v["type"], ("string", "integer"))
                self.assertTrue(v.get("description"), k)
                if "enum" in v:
                    self.assertTrue(v["enum"])
            self.assertIn("Call this", t["description"])
        self.assertEqual(names, {"serve_directions", "serve_summary", "list_points", "point_detail"})
        for t in chat.TOOLS:
            self.assertTrue(hasattr(chat.MatchData, t["name"]))

    def test_validate_input(self):
        ok = {"player": "A", "situation": "40-0", "score": "any", "serve": "1", "side": "ad"}
        self.assertIsNone(chat.validate_input("serve_directions", ok))
        self.assertIn("unexpected", chat.validate_input("serve_directions", dict(ok, extra=1)))
        self.assertIn("missing", chat.validate_input("serve_directions", {"player": "A"}))
        self.assertIn("one of", chat.validate_input("serve_directions", dict(ok, side="left")))
        self.assertIn("string", chat.validate_input("serve_directions", dict(ok, player=1)))
        self.assertIn("whole number", chat.validate_input("point_detail", {"point_id": "3"}))
        self.assertIn("whole number", chat.validate_input("point_detail", {"point_id": True}))
        self.assertIn("unknown tool", chat.validate_input("nope", {}))
        self.assertIn("not an object", chat.validate_input("point_detail", [1]))

    def test_run_tool_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _write(tmp, [_point(1, "A", "0-0", "A", [_serve(1, "in", "T")])])
            text, err = chat.run_tool(d, "point_detail", {"point_id": "x"})
            self.assertTrue(err)
            self.assertIn("INVALID_JSON", json.loads(text))
            text, err = chat.run_tool(d, "point_detail", {"point_id": 1})
            self.assertFalse(err)
            self.assertEqual(json.loads(text)["point"], 1)
            text, err = chat.run_tool(d, "point_detail", {"point_id": 5})
            self.assertTrue(err)

    def test_prompt_rules_present(self):
        p = chat.SYSTEM_PROMPT
        for w in ("count with every share", "never mix", "fewer than 10", "deuce side", "40-0 on the deuce side",
                  "data cannot answer", "not instructions",
                  # gap close step: no he or she, eye check in the overview's own words, per-side counts
                  "never write he, she", "men or women", "add nothing to them",
                  "how many checkers agreed", "points_served_in_group, which covers every side"):
            self.assertIn(w.lower(), p.lower(), w)
        self.assertNotIn("\u2014", p)
        self.assertNotIn("\u2013", p)


class TestPlainText(unittest.TestCase):
    def test_cleans_split_chunks(self):
        text = "# Short\nHe went **wide** most \u2014 7 of 11.\n## Next\n- T: 4 of 11, a 4-to-1 edge"
        ref = chat.PlainText()
        whole = ref.feed(text) + ref.flush()
        self.assertEqual(whole, "Short\nHe went wide most, 7 of 11.\nNext\n- T: 4 of 11, a 4-to-1 edge")
        for size in range(1, 8):
            c = chat.PlainText()
            out = "".join(c.feed(text[i:i + size]) for i in range(0, len(text), size)) + c.flush()
            self.assertEqual(out, whole, size)

    def test_keeps_single_star_and_inner_hash(self):
        c = chat.PlainText()
        self.assertEqual(c.feed("5") + c.feed("*") + c.feed(" ok, point #3") + c.flush(), "5* ok, point #3")


# ---------------------------------------------------------------------------
# match 1 output (skipped when the folder is not there)
# ---------------------------------------------------------------------------
@unittest.skipUnless((OUT1 / "match.json").exists(), "match 1 output not present")
class TestMatch1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = chat.MatchData(OUT1)

    def test_counts_equal_patterns(self):
        d = self.d
        sp = pt.compute(d.points, d.players, d.fmt)
        for P in ("A", "B"):
            for src in ("checked", "auto"):
                for sit, _ in pt.SITUATIONS:
                    for sl in pt.SLOTS:
                        for sd in pt.SIDE_KEYS:
                            g = pt.group(sp, P, src, sit, sl, sd)
                            mine = d.count(P, sit, "any", sd, sl)[0][src]
                            for k in pt.COUNT_KEYS:
                                self.assertEqual(g[k], mine[k], (P, src, sit, sl, sd, k))

    def test_overview_matches_patterns(self):
        d = self.d
        sp = pt.compute(d.points, d.players, d.fmt)
        ov = d.overview()
        for P in ("A", "B"):
            for src, S in sp["players"][P]["sources"].items():
                fs = S["first_serve_in"]
                got = ov["players"][P]["serve_basics"][chat.SRC_OUT[src]]["first_serve_in"]
                self.assertEqual(got, chat.share(fs["in"], fs["in"] + fs["fault"]))
                self.assertEqual(ov["players"][P]["serve_basics"][chat.SRC_OUT[src]]["double_faults"],
                                 S["double_faults"]["count"])
        self.assertLess(len(json.dumps(ov)), 20000)

    def test_no_impossible_side_in_data(self):
        d = self.d
        for P in ("A", "B"):
            for sit in ("40-0", "0-40", "30-30", "deuce", "adv_server", "adv_receiver", "first_point"):
                for sd in ("deuce", "ad"):
                    r = d.serve_directions(P, sit, "any", "all", sd)
                    if not r["side_rule"]["asked_side_possible"]:
                        self.assertEqual(r["points_served_in_group"][sd], 0, (P, sit, sd))

    def test_side_counts_same_whatever_side_is_asked(self):
        d = self.d
        for P in ("A", "B"):
            for sit in chat.SITUATION_KEYS:
                both = d.serve_directions(P, sit, "any", "1", "both")["points_served_in_group"]
                n = d.list_points(P, sit, "any", "both", "any", "any", 0, 1)["points_matching"]
                self.assertEqual(both["total"], n, (P, sit))
                for sd in ("deuce", "ad"):
                    r = d.serve_directions(P, sit, "any", "1", sd)
                    self.assertEqual(r["points_served_in_group"], both, (P, sit, sd))
                    self.assertEqual(r["points_served_on_asked_side"], both[sd], (P, sit, sd))


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------
class TestKey(unittest.TestCase):
    def _env(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_parse_variants(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for text in ("ANTHROPIC_API_KEY=%s\n" % FAKE_KEY, 'ANTHROPIC_API_KEY="%s"\n' % FAKE_KEY,
                         "# c\nexport ANTHROPIC_API_KEY='%s'\n" % FAKE_KEY,
                         "OTHER=1\nANTHROPIC_API_KEY = %s # note\n" % FAKE_KEY):
                self.assertEqual(chat.load_api_key(self._env(text)), FAKE_KEY, text)
            self.assertIsNone(chat.load_api_key(self._env("ANTHROPIC_API_KEY=\n")))
            self.assertIsNone(chat.load_api_key("/nonexistent/.env"))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": FAKE_KEY}, clear=True):
            self.assertEqual(chat.load_api_key("/nonexistent/.env"), FAKE_KEY)

    def test_missing_key_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, [_point(1, "A", "0-0", "A", [_serve(1, "in", "T")])])
            env = self._env("NOTHING=1\n")
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch("sys.stderr") as err:
                code = chat.main([tmp, "--ask", "hi", "--env-file", env])
            self.assertEqual(code, 2)
            text = "".join(c.args[0] for c in err.write.call_args_list)
            self.assertIn("No API key found", text)

    def test_client_pins_base_url_and_no_retries(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://example.invalid"}):
            c = chat.make_client(FAKE_KEY)
        self.assertEqual(str(c.base_url).rstrip("/"), chat.API_BASE_URL)
        self.assertEqual(c.max_retries, 0)


# ---------------------------------------------------------------------------
# chat loop with a fake client
# ---------------------------------------------------------------------------
def _usage(i=100, o=20):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=5, cache_read_input_tokens=7)


def _msg(content, stop, details=None):
    return SimpleNamespace(content=content, stop_reason=stop, usage=_usage(), stop_details=details)


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool(i, name, inp):
    return SimpleNamespace(type="tool_use", id="toolu_%d" % i, name=name, input=inp)


class FakeStream:
    def __init__(self, item):
        self.item = item

    def __enter__(self):
        if isinstance(self.item, BaseException):
            raise self.item
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self.item.content:
            if b.type == "text":
                yield SimpleNamespace(type="text", text=b.text)

    def get_final_message(self):
        return self.item


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(dict(kw, messages=list(kw["messages"])))
        return FakeStream(self.script.pop(0))


def _req():
    import httpx2
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, code, message):
    import httpx2
    return cls(message, response=httpx2.Response(code, request=_req()), body=None)


class TestSession(TmpMatch):
    def setUp(self):
        super().setUp()
        self.d = self.data([_point(1, "A", "0-0", "A", [_serve(1, "in", "T")], review=CHECK_ALL),
                            _point(2, "A", "15-0", "B", [_serve(1, "in", "wide")], review=CHECK_ALL)])
        self.log = Path(self._tmp.name) / "usage.jsonl"

    def session(self, script, **kw):
        client = FakeClient(script)
        usage = kw.pop("usage", None) or chat.UsageLog(self.log, "t")
        return chat.ChatSession(self.d, client=client, usage=usage, sleep=lambda s: None, **kw), client

    def test_tool_round_then_answer(self):
        ok = {"player": "A", "situation": "all", "score": "any", "serve": "all", "side": "both"}
        s, c = self.session([_msg([_tool(1, "serve_directions", ok)], "tool_use"),
                             _msg([_text("Ann served **T** on 1 of 2 \u2014 50%.")], "end_turn")])
        events = []
        r = s.ask("Where does Ann serve?", lambda k, v: events.append((k, v)))
        self.assertIsNone(r["error"])
        self.assertEqual(r["answer"], "Ann served T on 1 of 2, 50%.")
        self.assertEqual(r["calls"], 2)
        self.assertEqual([t["name"] for t in r["tools"]], ["serve_directions"])
        kw = c.calls[0]
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["thinking"], {"type": "adaptive"})
        self.assertEqual(kw["output_config"], {"effort": "high"})
        self.assertEqual(kw["cache_control"], {"type": "ephemeral"})
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("tool_choice", kw)
        self.assertIs(kw["tools"], chat.TOOLS)
        first = kw["messages"][0]["content"]
        self.assertIn("<match_overview>", first[0]["text"])
        self.assertEqual(first[1]["text"], "Where does Ann serve?")
        roles = [m["role"] for m in s.messages]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])
        res = s.messages[2]["content"][0]
        self.assertEqual(res["tool_use_id"], "toolu_1")
        self.assertIn("eye_checked", res["content"])
        lines = [json.loads(x) for x in self.log.read_text().splitlines()]
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertEqual(set(ln), {"label", "input_tokens", "output_tokens", "cache_creation_input_tokens",
                                       "cache_read_input_tokens"})
        self.assertEqual(lines[0]["label"], "t q1/1 tool_use")
        self.assertIn(("tool", {"name": "serve_directions", "input": ok}), events)

    def test_parallel_tools_one_user_message(self):
        a = {"player": "A"}
        b = {"player": "B"}
        s, c = self.session([_msg([_tool(1, "serve_summary", a), _tool(2, "serve_summary", b)], "tool_use"),
                             _msg([_text("done")], "end_turn")])
        s.ask("Compare them")
        self.assertEqual(len(s.messages[2]["content"]), 2)
        self.assertEqual([x["tool_use_id"] for x in s.messages[2]["content"]], ["toolu_1", "toolu_2"])

    def test_second_question_has_no_overview(self):
        s, c = self.session([_msg([_text("one")], "end_turn"), _msg([_text("two")], "end_turn")])
        r1 = s.ask("hello")
        self.assertIn("match overview", r1["note"])
        r2 = s.ask("again")
        self.assertIn("earlier answers", r2["note"])
        self.assertEqual(c.calls[1]["messages"][2], {"role": "user", "content": "again"})
        self.assertEqual(c.calls[1]["messages"][:2], c.calls[0]["messages"][:1] + [s.messages[1]])

    def test_refusal_rolls_back(self):
        det = SimpleNamespace(category="cyber", explanation="x")
        s, c = self.session([_msg([_text("one")], "end_turn"), _msg([], "refusal", det)])
        s.ask("first")
        before = list(s.messages)
        r = s.ask("bad")
        self.assertEqual(r["error_kind"], "refusal")
        self.assertIn("cyber", r["error"])
        self.assertEqual(s.messages, before)

    def test_max_tokens(self):
        s, c = self.session([_msg([_tool(1, "serve_summary", {"player": "A"})], "max_tokens")])
        r = s.ask("q")
        self.assertEqual(r["error_kind"], "max_tokens")
        self.assertEqual(s.messages, [])
        s, c = self.session([_msg([_text("long answer")], "max_tokens")])
        r = s.ask("q")
        self.assertIsNone(r["error"])
        self.assertIn("cut off", r["note"])
        self.assertEqual(len(s.messages), 2)

    def test_network_error_retried_and_logged(self):
        import anthropic
        s, c = self.session([anthropic.APIConnectionError(request=_req()), _msg([_text("ok")], "end_turn")])
        events = []
        r = s.ask("q", lambda k, v: events.append(k))
        self.assertIsNone(r["error"])
        self.assertEqual(r["calls"], 2)
        self.assertIn("round_reset", events)
        labels = [json.loads(x)["label"] for x in self.log.read_text().splitlines()]
        self.assertEqual(labels, ["t q1/1 network_error", "t q1/1 end_turn"])

    def test_network_down_gives_clean_error(self):
        import anthropic
        s, c = self.session([anthropic.APIConnectionError(request=_req()) for _ in range(3)])
        r = s.ask("q")
        self.assertEqual(r["error_kind"], "network")
        self.assertEqual(r["calls"], 3)
        self.assertEqual(s.messages, [])

    def test_auth_and_credit_errors_not_retried(self):
        import anthropic
        s, c = self.session([_status_error(anthropic.AuthenticationError, 401, "invalid x-api-key")])
        r = s.ask("q")
        self.assertEqual((r["error_kind"], r["calls"]), ("auth", 1))
        self.assertNotIn(FAKE_KEY, r["error"])
        s, c = self.session([_status_error(anthropic.BadRequestError, 400,
                                           "Your credit balance is too low to access the Anthropic API.")])
        r = s.ask("q")
        self.assertEqual((r["error_kind"], r["calls"]), ("no_credit", 1))
        s, c = self.session([_status_error(anthropic.InternalServerError, 529, "Overloaded"),
                             _msg([_text("ok")], "end_turn")])
        r = s.ask("q")
        self.assertIsNone(r["error"])

    def test_budget(self):
        u = chat.UsageLog(self.log, "t", max_calls=1)
        s, c = self.session([_msg([_tool(1, "serve_summary", {"player": "A"})], "tool_use")], usage=u)
        r = s.ask("q")
        self.assertEqual(r["error_kind"], "budget")
        self.assertEqual(len(c.calls), 1)
        self.assertEqual(s.messages, [])
        u2 = chat.UsageLog(self.log, "t", file_limit=1)
        s, c = self.session([], usage=u2)
        self.assertEqual(s.ask("q")["error_kind"], "budget")
        self.assertEqual(c.calls, [])

    def test_bad_tool_input_goes_back_as_error(self):
        s, c = self.session([_msg([_tool(1, "point_detail", {"point_id": "one"})], "tool_use"),
                             _msg([_text("sorry")], "end_turn")])
        s.ask("q")
        res = s.messages[2]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertIn("INVALID_JSON", res["content"])

    def test_too_many_calls(self):
        script = [_msg([_tool(i, "serve_summary", {"player": "A"})], "tool_use") for i in range(3)]
        s, c = self.session(script, max_calls_per_question=3)
        r = s.ask("q")
        self.assertEqual(r["error_kind"], "too_many_calls")
        self.assertEqual(s.messages, [])

    def test_no_client_and_bad_questions(self):
        s = chat.ChatSession(self.d, client=None)
        self.assertEqual(s.ask("q")["error_kind"], "no_key")
        self.assertEqual(s.ask("   ")["error_kind"], "empty")
        self.assertEqual(s.ask("x" * 5000)["error_kind"], "too_long")
        self.assertEqual(s.messages, [])
        self.assertNotIn(FAKE_KEY, repr(s))


# ---------------------------------------------------------------------------
# local page
# ---------------------------------------------------------------------------
class TestWeb(TmpMatch):
    def setUp(self):
        super().setUp()
        d = self.data([_point(1, "A", "0-0", "A", [_serve(1, "in", "T")], review=CHECK_ALL)])
        self.client = FakeClient([_msg([_text("Ann won.")], "end_turn")])
        self.session = chat.ChatSession(d, client=self.client, sleep=lambda s: None)
        port_ref = {"port": 0}
        self.httpd = chat.ThreadingHTTPServer(("127.0.0.1", 0), chat.make_handler(self.session, port_ref))
        self.port = port_ref["port"] = self.httpd.server_address[1]
        self.th = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.th.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def test_page(self):
        st, h, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("nonce-", h["Content-Security-Policy"])
        html = body.decode()
        self.assertIn("Ann against Bea", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("\u2014", html)
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_guards(self):
        good = {"Content-Type": "application/json", "X-Serve-IQ": "1"}
        q = json.dumps({"question": "who won?"})
        self.assertEqual(self.req("GET", "/", host="evil.example:80")[0], 403)
        self.assertEqual(self.req("POST", "/api/ask", q, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.req("POST", "/api/ask", q, dict(good, Origin="http://evil.example"))[0], 403)
        self.assertEqual(self.req("POST", "/api/ask", q, {"X-Serve-IQ": "1", "Content-Type": "text/plain"})[0],
                         415)
        self.assertEqual(self.req("POST", "/api/ask", "x" * 20000, good)[0], 413)
        self.assertEqual(self.req("GET", "/match.json")[0], 404)
        self.assertEqual(self.client.calls, [])

    def test_ask_streams(self):
        good = {"Content-Type": "application/json", "X-Serve-IQ": "1",
                "Origin": "http://127.0.0.1:%d" % self.port}
        st, h, body = self.req("POST", "/api/ask", json.dumps({"question": "who won?"}), good)
        self.assertEqual(st, 200)
        evs = [json.loads(x) for x in body.decode().splitlines() if x.strip()]
        self.assertIn({"type": "text", "text": "Ann won."}, evs)
        self.assertEqual(evs[-1]["type"], "done")
        self.assertEqual(evs[-1]["calls"], 1)
        st, h, body = self.req("POST", "/api/reset", "{}", good)
        self.assertEqual(st, 200)
        self.assertEqual(self.session.messages, [])


if __name__ == "__main__":
    unittest.main()
