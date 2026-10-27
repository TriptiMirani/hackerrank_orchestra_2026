"""Northstar deletion-audit case viewer (Hugging Face Space).

Reads cases.json, exported by scripts/export_ui_data.py from the same deterministic audit that produced output.csv.
No model, no dataset, no secret runs here: this is a read-only view of the evidence trail behind every answer.
"""
from __future__ import annotations

import json
from pathlib import Path

import gradio as gr

DATA = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
CASES = {c["case_id"]: c for c in DATA["cases"]}
S = DATA["summary"]

ITEM_HEADERS = ["resource", "system", "category", "rule", "verdict", "state at audit_at", "as of", "evidence",
                "evidence file", "linked by"]
STEP_HEADERS = ["#", "phase", "system", "resource", "operation", "state", "evidence"]


def label(c: dict) -> str:
    return f"{c['case_id']}  ·  {c['status']}  ·  {c['split']}"


def choices(split: str, status: str) -> list[str]:
    return [label(c) for c in DATA["cases"]
            if (split == "all" or c["split"] == split) and (status == "all" or c["status"] == status)]


def show(choice: str | None):
    if not choice:
        return "", "", [], [], ""
    c = CASES[choice.split("  ·  ")[0]]
    r = c["request"]
    head = (f"## {c['case_id']} → **{c['status']}**\n\n"
            f"- **Request email:** `{r['email']}`; requested {r['requested_at'][:16]}, workflow claimed "
            f"**{r['reported_status']}** at {r['reported_at'][:16]}, audited **as of {r['audit_at'][:16]}**\n"
            f"- **Intent:** {c['intent']} ({c['intent_reason']})\n\n> {r['text']}")
    i = c["identity"]
    ident = "_Not a deletion request: no identity resolved, no steps (`[]`)._" if i is None else (
        f"**{i['user_id']}** (matched via {i['matched_via'].replace('_', ' ')})\n\n"
        f"- emails: {', '.join(f'`{e}`' for e in i['emails'])}\n- phones: {', '.join(i['phones'])}\n"
        f"- names: {', '.join(i['names'])}\n- email hashes (sha256 of the email): {', '.join(i['email_hashes'])}")
    if c["unresolved"]:
        ident += f"\n\n_Linked but no evidence of state (not acted on): {', '.join(c['unresolved'])}_"
    items = [[x["resource_id"], x["system_id"], x["category"], x["treatment"], x["verdict"], x["state"],
              x["as_of"][:16], x["evidence_id"], x["evidence_file"], f"{x['link']}: {x['link_detail'][:60]}"]
             for x in c["items"]]
    steps = [[s["step_no"], s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"],
              s["evidence_id"]] for s in c["steps"]]
    exp = ""
    if c["expected"]:
        e = c["expected"]
        same = e["status"] == c["status"] and e["steps"] == c["steps"]
        exp = (f"### Sample answer key: {'✅ identical' if same else '⚠️ differs'}\n\nexpected status **{e['status']}**"
               f"\n\n" + "\n".join(f"{s['step_no']}. {s['phase']} · {s['operation']} · {s['system_id']}/"
                                   f"{s['resource_id']} · {s['evidence_id'] or '—'}" for s in e["steps"]))
    return head, ident, items, steps, exp


def summary_md() -> str:
    s, f = S["sample"], S["final_run"]
    fa = " · ".join(f"{k} {v:.1%}" for k, v in S["sample_field_accuracy"].items())
    return (f"### Measured results\n\n| metric | value |\n|---|---|\n"
            f"| sample status accuracy | {s['status_accuracy']:.1%} |\n"
            f"| sample step precision / recall / F1 | {s['step_precision']:.1%} / {s['step_recall']:.1%} / "
            f"{s['step_f1']:.1%} |\n| sample exact sequence / edit similarity | {s['sequence_exact']:.1%} / "
            f"{s['sequence_similarity']:.1%} |\n| custom cases | {S['custom_cases_pass'].replace('**', '').strip()} |\n"
            f"| final run | {f['rows']} rows, {f['statuses']}, {f['steps']} steps, {f['llm_calls']} LLM calls, "
            f"{f['seconds']} s |\n| output.csv sha256 | `{f['output_sha256'][:16]}…` |\n\nField accuracy: {fa}")


with gr.Blocks(title="Northstar deletion audit") as demo:
    gr.Markdown("# Northstar deletion-audit case viewer\nEvery answer below comes from the deterministic audit that "
                "produced `output.csv`; each finding cites the evidence file that proves it. Read-only: no model, "
                "no dataset and no secret run in this Space.")
    with gr.Tab("Case"):
        with gr.Row():
            split = gr.Radio(["all", "eval", "sample"], value="all", label="Split")
            status = gr.Radio(["all", "COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"], value="all", label="Status")
        case = gr.Dropdown(choices("all", "all"), value=choices("all", "all")[0], label="Case", filterable=True)
        head = gr.Markdown()
        with gr.Accordion("Resolved identifiers (current + historical, as of audit_at)", open=True):
            ident = gr.Markdown()
        gr.Markdown("### Linked resources: state at audit_at and policy verdict")
        items = gr.Dataframe(headers=ITEM_HEADERS, interactive=False, wrap=True)
        gr.Markdown("### steps_json (as submitted)")
        steps = gr.Dataframe(headers=STEP_HEADERS, interactive=False, wrap=True)
        expected = gr.Markdown()
    with gr.Tab("Scores"):
        gr.Markdown(summary_md())

    def refilter(sp, st):
        opts = choices(sp, st)
        return gr.Dropdown(choices=opts, value=opts[0] if opts else None)

    split.change(refilter, [split, status], case)
    status.change(refilter, [split, status], case)
    case.change(show, case, [head, ident, items, steps, expected])
    demo.load(show, case, [head, ident, items, steps, expected])

if __name__ == "__main__":
    demo.launch()
