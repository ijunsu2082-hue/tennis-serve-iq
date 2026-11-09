"""The coach's page: player cards, serve maps and the score situation bars."""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tennis import report as rp  # noqa: E402

NAMES = {"A": "Kim", "B": "Ruiz"}


def note(key, player="A", kind="weakness", p=0.01, conf="medium", effect=-20.0, **kw):
    d = {"key": key, "player": player, "kind": kind, "chance_p": p, "confidence": conf, "effect": effect,
         "k": 8, "n": 36, "pct": 22.2, "comparison_pct": 50.0, "comparison_n": 10, "text": "",
         "small_sample": False, "checked": {"status": "all"}}
    d.update(kw)
    return d


class TestCards(unittest.TestCase):
    def test_weak_return_becomes_a_short_order_for_the_opponent(self):
        c = rp.coach_note(note("ret_wing_bh", effect=15.0), NAMES)
        self.assertEqual(c["plan"], "Serve to Kim's backhand")
        self.assertEqual(c["label"], "Backhand return")
        self.assertEqual(c["kind"], "weakness")
        self.assertEqual(c["bars"][0]["of"], "8 of 36")
        self.assertEqual(c["bars"][1]["of"], "5 of 10")     # counted back from its percent
        self.assertNotIn("%", c["plan"])

    def test_zone_notes_read_as_orders(self):
        bad = rp.coach_note(note("zone_eff_2_deuce_body"), NAMES)
        good = rp.coach_note(note("zone_eff_1_ad_T", kind="strength", effect=20.0), NAMES)
        self.assertEqual(bad["plan"], "Attack 2nd serves to the body on the deuce side")
        self.assertEqual(good["plan"], "Watch for 1st serves down the T on the ad side")

    def test_likely_luck_is_left_out(self):
        cards = rp.player_cards([note("ret_wing_bh", effect=15.0, p=0.2),
                                 note("net", p=0.45)], NAMES)
        self.assertEqual(cards["A"], {"strengths": [], "weaknesses": [], "plan": []})

    def test_strength_of_evidence_is_kept(self):
        c = rp.coach_note(note("net", conf="low", small_sample=True, checked={"status": "none"}), NAMES)
        self.assertEqual((c["dots"], c["level"]), (1, "Early sign"))
        self.assertEqual(c["notes"], ["small sample", "computer only, not checked"])

    def test_a_weakness_leads_the_plan_and_lists_are_capped(self):
        notes = [note("rally_long", kind="strength", effect=20.0), note("winner_wing", kind="strength", effect=16.0,
                                                                        text="came off the forehand"),
                 note("ret_wing_bh", effect=15.0)]
        notes += [note("zone_eff_2_ad_%s" % z, kind="strength", effect=20.0) for z in ("T", "body", "wide")]
        cards = rp.player_cards(notes, NAMES)
        self.assertEqual(cards["A"]["plan"][0]["plan"], "Serve to Kim's backhand")
        self.assertEqual(len(cards["A"]["plan"]), rp.PLAN_ITEMS)
        self.assertEqual(len(cards["A"]["strengths"]), rp.CARD_ITEMS)

    def test_hand_assumption_is_said(self):
        c = rp.coach_note(note("ret_wing_bh", effect=15.0), NAMES, {"A": {"hand": "R", "hand_given": False}})
        self.assertIn("assumes right-handed", c["notes"])
        c = rp.coach_note(note("ret_wing_bh", effect=15.0), NAMES, {"A": {"hand": "R", "hand_given": True}})
        self.assertEqual(c["notes"], [])


def zones(n, counts):
    return {"deuce": {"n": n, **{z: {"count": c, "share": {"pct": 100.0 * c / n}, "won": {"k": 0, "n": 0}}
                                 for z, c in counts.items()}}, "ad": {"n": 0}}


class TestPictures(unittest.TestCase):
    def test_a_handful_of_serves_is_never_shaded_as_main(self):
        self.assertNotIn('class="hot"', rp.svg_serve_map(zones(6, {"T": 5, "body": 1, "wide": 0}), "x"))
        self.assertIn("too few", rp.svg_serve_map(zones(6, {"T": 5, "body": 1, "wide": 0}), "x"))
        self.assertEqual(rp.svg_serve_map(zones(20, {"T": 15, "body": 3, "wide": 2}), "x").count('class="hot"'), 1)

    def test_score_bars_do_not_mix_checked_and_computer_values(self):
        grp = lambda t: {"1": {"both": {"T": t, "body": 0, "wide": 0}}}
        sp = {"small": 10, "players": {"A": {"sources": {"checked": {"groups": {"all": grp(20), "break_point": grp(4)}},
                                                         "auto": {"groups": {"all": grp(3)}}}}}}
        big = rp.big_point_rows(sp)
        self.assertFalse(big["A"]["computer_only"])
        self.assertEqual(big["A"]["rows"][0]["seen"], 20)
        self.assertEqual(big["A"]["rows"][2]["seen"], 4)
        self.assertTrue(big["A"]["rows"][2]["too_small"])


class TestPage(unittest.TestCase):
    def test_long_parts_are_folded_away(self):
        from test_analytics import build_sample_match  # noqa
        m = build_sample_match()
        with tempfile.TemporaryDirectory() as d:
            rp.render(m, d)
            with open(os.path.join(d, "report.html"), encoding="utf-8") as f:
                html = f.read()
        names = m["meta"]["players"]
        self.assertIn("How to play against %s" % names["A"]["name"], html)
        self.assertIn("Strengths", html)
        self.assertIn("Weaknesses", html)
        top = html[:html.index('class="moreh"')]
        for gone in ('id="ptab"', "How sure a note is", "Fisher"):
            self.assertNotIn(gone, top)
        self.assertNotIn("Palatino", html)
        self.assertEqual(html.count("<details"), html.count("</details>"))


if __name__ == "__main__":
    unittest.main()
