from probezen.dependencies import analyze_usages, discover, inventory_mapping
from probezen.impact import explain_impact
from probezen.models import Finding


def test_discovers_urls_sdks_env_hosts_deduplicates_and_ignores(tmp_path):
    (tmp_path / ".env.example").write_text("BILLING_URL=https://billing.internal.example/v2\n")
    (tmp_path / ".gitignore").write_text("ignored/**\n")
    source = tmp_path / "src"
    source.mkdir()
    (source / "client.ts").write_text(
        """import OpenAI from "openai";
fetch("https://api.openai.com/v1/responses");
fetch("https://api.openai.com/v1/models");
fetch(process.env.BILLING_URL);
"""
    )
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "vendor.ts").write_text('fetch("https://should-not-appear.example")')
    generated = tmp_path / "node_modules" / "pkg"
    generated.mkdir(parents=True)
    (generated / "index.js").write_text('fetch("https://also-ignored.example")')

    result = discover(tmp_path)

    assert result.ecosystem == "TypeScript / Node.js"
    assert [item.id for item in result.dependencies] == ["billing-internal-example", "openai"]
    openai = result.dependencies[1]
    assert openai.sdk == "openai"
    assert openai.hosts == ["api.openai.com"]
    assert openai.version_pinned is True
    assert len(openai.discovered_from) == 3
    inventory = inventory_mapping(result)
    assert inventory["openai"]["provider"] == "openai"


def test_usage_analysis_is_conservative_and_recognizes_guards():
    usages = analyze_usages(
        """const total = response.price * quantity;
response.customer.email.toLowerCase();
const first = response.items[0];
if (response.status === "active") render();
console.log(response.description);
if (response.profile.name) { response.profile.name.toLowerCase(); }
""",
        "src/client.ts",
    )
    pairs = {(item.field, item.kind) for item in usages}
    assert ("price", "numeric") in pairs
    assert ("customer.email", "string_method") in pairs
    assert ("items", "array_index") in pairs
    assert ("status", "enum") in pairs
    assert not any(item.field == "description" for item in usages)
    guarded = next(item for item in usages if item.field == "profile.name")
    assert guarded.guarded is True
    assert guarded.confidence == "medium"


def test_impact_promotes_matching_unguarded_code(tmp_path):
    source = tmp_path / "src" / "checkout.ts"
    source.parent.mkdir()
    source.write_text(
        'fetch("https://vendor.example/v1/products");\nconst total = response.price * 2;\n'
    )
    dependency = discover(tmp_path).dependencies[0]
    finding = Finding("breaking", "type_change", "products[].price", "number", "string")

    explained = explain_impact(finding, dependency)

    assert explained.level == "high"
    assert explained.confidence == "high"
    assert explained.affected_code[0]["line"] == 2
    assert "arithmetic" in explained.reason


def test_multiple_dependencies_in_one_file_use_nearest_preceding_client(tmp_path):
    source = tmp_path / "src" / "clients.ts"
    source.parent.mkdir()
    source.write_text(
        """fetch("https://api.stripe.com/v1/prices");
const total = response.price * 2;
fetch("https://billing.internal.example/v1/customer");
response.customer.email.toLowerCase();
"""
    )
    dependencies = {item.id: item for item in discover(tmp_path).dependencies}
    assert [item.field for item in dependencies["stripe"].usages] == ["price"]
    assert [item.field for item in dependencies["billing-internal-example"].usages] == [
        "customer.email"
    ]
