import seal

BODY = "---\nspec_format_version: v2.1\n{seal}---\n\n# Spec\n\n- FR-1: x\n"


def _write(tmp_path, seal_line=""):
    p = tmp_path / "spec.md"
    p.write_text(BODY.format(seal=seal_line))
    return p


def test_unsealed_spec_builds_with_a_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("TROWEL_DREW_VERIFIER", "none")
    assert seal.check(_write(tmp_path))["status"] == "unsealed"


def test_sealed_spec_holds(tmp_path, monkeypatch):
    monkeypatch.setenv("TROWEL_DREW_VERIFIER", "none")
    digest = seal.spec_sha256(BODY.format(seal=""))
    assert seal.check(_write(tmp_path, f"sealed_by: validate-spec.py sha256:{digest}\n"))["status"] == "sealed"


def test_edited_spec_breaks_its_seal(tmp_path, monkeypatch):
    monkeypatch.setenv("TROWEL_DREW_VERIFIER", "none")
    p = _write(tmp_path, f"sealed_by: validate-spec.py sha256:{'0' * 64}\n")
    r = seal.check(p)
    assert r["status"] == "broken" and "changed after drew sealed it" in r["message"]


def test_malformed_seal_is_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("TROWEL_DREW_VERIFIER", "none")
    assert seal.check(_write(tmp_path, "sealed_by: hand\n"))["status"] == "broken"
