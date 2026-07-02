def test_ares_skin_imports_and_exposes_branding():
    from cli.skin_engine import load_skin

    skin = load_skin("ares")

    assert skin.name == "ares"
    assert skin.get_branding("agent_name") == "Ares Agent"
    assert skin.get_branding("prompt_symbol")
