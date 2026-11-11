from pathlib import Path

import pytest

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from printagent import store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")


@pytest.fixture(scope="session", autouse=True)
def samples():
    if not (SAMPLES / "01_good_card.tif").exists():
        import runpy
        runpy.run_path(str(SAMPLES / "make_samples.py"), run_name="__main__")


def _pf(name):
    from printagent.preflight import preflight
    return preflight(SAMPLES / name, "business-cards")


def _ids(report, severity):
    return {c["id"] for c in report["checks"] if c["severity"] == severity}


@pytest.mark.parametrize("name", ["01_good_card.tif", "02_good_card.pdf"])
def test_good_artwork_passes(name):
    assert _pf(name)["status"] == "pass"


@pytest.mark.parametrize("name,error", [
    ("03_no_bleed.png", "bleed"),
    ("04_empty_bleed.png", "bleed_fill"),
    ("06_low_res.jpg", "resolution"),
    ("07_wrong_size.png", "size"),
    ("08_font_not_embedded.pdf", "fonts"),
])
def test_bad_artwork_is_caught(name, error):
    r = _pf(name)
    assert r["status"] == "fail" and error in _ids(r, "error")


def test_text_near_cut_is_a_warning():
    r = _pf("05_text_near_cut.png")
    assert "safe_zone" in _ids(r, "warning") and r["printable"]


def test_typo_is_invisible_to_rules_and_needs_the_llm():
    # The deterministic checks cannot read; render_proof exists for this.
    assert _pf("09_typo_passes_checks.tif")["status"] == "pass"


@pytest.mark.parametrize("name", ["03_no_bleed.png", "04_empty_bleed.png", "08_font_not_embedded.pdf"])
def test_fixer_makes_file_print_ready(name, tmp_path):
    from printagent.fixer import fix
    r = fix(SAMPLES / name, "business-cards", tmp_path)
    assert r["fixed"] and r["status_after"] == "pass"


def test_fixer_never_fakes_resolution(tmp_path):
    from printagent.fixer import fix
    r = fix(SAMPLES / "06_low_res.jpg", "business-cards", tmp_path)
    assert r["status_after"] == "fail" and "Low resolution" in r["not_fixed"]


def test_fixer_refuses_to_crop(tmp_path):
    from printagent.fixer import fix
    assert fix(SAMPLES / "07_wrong_size.png", "business-cards", tmp_path)["fixed"] is False


def test_quote_tiers_and_vat():
    from printagent.pricing import quote
    q = quote("business-cards", 500)
    assert q["subtotal_eur"] == pytest.approx(500 * 0.07)
    assert q["total_eur"] == pytest.approx(q["subtotal_eur"] * 1.21, abs=0.01)
    assert "1000 units" in q["next_tier_hint"]
    with pytest.raises(ValueError):
        quote("business-cards", 10)


def test_search_finds_products():
    from printagent.catalog import search
    assert search("visiting cards for my bakery")[0]["id"].startswith("business-cards")
    assert search("A5 leaflet")[0]["id"] == "flyer-a5"


def test_order_gate(tmp_path):
    from printagent import store
    from printagent.preflight import preflight
    from printagent.pricing import quote

    bad = store.save_preflight(preflight(SAMPLES / "03_no_bleed.png", "business-cards"))
    with pytest.raises(ValueError, match="failed preflight"):
        store.create_order("business-cards", quote("business-cards", 500), bad, None, "")

    good = store.save_preflight(preflight(SAMPLES / "01_good_card.tif", "business-cards"))
    with pytest.raises(ValueError, match="checked for"):
        store.create_order("flyer-a5", quote("flyer-a5", 500), good, None, "")

    order = store.create_order("business-cards", quote("business-cards", 500), good, None, "")
    assert order["status"] == "draft" and order["id"].startswith("HP-")

    # file edited after the check -> must be re-checked
    copy = tmp_path / "card.tif"
    copy.write_bytes((SAMPLES / "01_good_card.tif").read_bytes())
    pid = store.save_preflight(preflight(copy, "business-cards"))
    copy.write_bytes(copy.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="changed"):
        store.create_order("business-cards", quote("business-cards", 500), pid, None, "")


def test_web_flow(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from printagent import web
    monkeypatch.setattr(web, "WORK", tmp_path / "web")
    c = TestClient(web.app)
    assert "printagent" in c.get("/").text
    assert "03_no_bleed.png" in c.get("/api/samples").json()

    r = c.post("/api/preflight-sample", json={"name": "03_no_bleed.png"}).json()
    assert r["report"]["status"] == "fail" and c.get(r["proof_url"]).status_code == 200

    f = c.post("/api/fix", json={"job": r["job"], "product_id": "business-cards"}).json()
    assert f["fixed"] and f["after"]["report"]["status"] == "pass"
    pdf = c.get(f["download_url"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    up = c.post("/api/preflight", data={"product_id": "business-cards"},
                files={"file": ("my card.png", (SAMPLES / "05_text_near_cut.png").read_bytes(), "image/png")})
    assert up.json()["report"]["status"] == "pass_with_warnings"

    assert c.post("/api/preflight", data={"product_id": "business-cards"},
                  files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert c.get(f"/files/{r['job']}/..%2F..%2Fsecret").status_code == 404
    assert c.post("/api/quote", json={"product_id": "business-cards", "quantity": 500}).json()["total_eur"] > 0


class _FakeClient:
    """Stands in for anthropic.Anthropic in tests (no network, no key)."""
    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        block = type("B", (), {"type": "text", "text": self.reply})()
        return type("M", (), {"content": [block]})()


def test_ai_review_parses_model_output():
    from printagent import ai_review
    reply = ('Here you go: {"issues": [{"kind": "typo", "severity": "error", '
             '"text": "Rotterdma should be Rotterdam", "suggestion": "Fix the city name"}], '
             '"transcript": "Bakkerij Moerman ... Rotterdma"}')
    fake = _FakeClient(reply)
    r = ai_review.review(SAMPLES / "09_typo_passes_checks.tif", "business-cards", client=fake)
    assert r["checks"][0]["title"] == "Possible typo" and r["checks"][0]["severity"] == "error"
    assert "Rotterdma" in r["transcript"]
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image" and "Business Cards" in content[1]["text"]
    # garbage from the model never crashes the page
    assert ai_review.review(SAMPLES / "01_good_card.tif", "business-cards",
                            client=_FakeClient("sorry, no json"))["checks"] == []


def test_web_ai_review(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from printagent import ai_review, web
    monkeypatch.setattr(web, "WORK", tmp_path / "web")
    c = TestClient(web.app)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert c.get("/api/config").json() == {"ai_review": False}
    job = c.post("/api/preflight-sample", json={"name": "09_typo_passes_checks.tif"}).json()["job"]
    assert c.post("/api/ai-review", json={"job": job, "product_id": "business-cards"}).status_code == 400

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(ai_review, "review", lambda src, pid: {"model": "m", "checks": [], "transcript": str(src)})
    r = c.post("/api/ai-review", json={"job": job, "product_id": "business-cards"}).json()
    assert r["transcript"].endswith("09_typo_passes_checks.tif")


def test_dotenv(tmp_path, monkeypatch):
    from printagent.ai_review import load_dotenv
    monkeypatch.delenv("PA_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text('# comment\nPA_TEST_KEY="abc"\n')
    load_dotenv(tmp_path / ".env")
    import os
    assert os.environ["PA_TEST_KEY"] == "abc"
