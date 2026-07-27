from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from evo_cli.console import console

TITLE_FIELDS = ("title", "name", "question")
LINK_FIELDS = ("link", "product_link", "original", "serpapi_link", "thumbnail")
SNIPPET_FIELDS = ("snippet", "description", "answer")
META_FIELDS = ("source", "date", "price", "displayed_link", "duration", "rating", "reviews", "extracted_price")


def _first(item, fields):
    for field in fields:
        value = item.get(field)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return None


def item_title(item):
    title = _first(item, TITLE_FIELDS)
    if title:
        return title
    info = item.get("publication_info")
    if isinstance(info, dict):
        return _first(info, ("title", "summary")) or "(no title)"
    return "(no title)"


def item_link(item):
    return _first(item, LINK_FIELDS)


def item_snippet(item):
    snippet = _first(item, SNIPPET_FIELDS)
    if snippet:
        return snippet
    info = item.get("publication_info")
    if isinstance(info, dict):
        return _first(info, ("summary",))
    return None


def item_meta(item):
    parts = []
    for field in META_FIELDS:
        value = item.get(field)
        if isinstance(value, (str, int, float)) and str(value).strip():
            text = str(value).strip()
            parts.append(f"{text} reviews" if field == "reviews" else text)
    source = item.get("source")
    if isinstance(source, dict):
        name = _first(source, ("name",))
        if name:
            parts.insert(0, name)
    return " · ".join(dict.fromkeys(parts))


def links(payload, result_key):
    return [link for link in (item_link(item) for item in payload.get(result_key) or []) if link]


def render_answer_box(payload):
    box = payload.get("answer_box")
    if not isinstance(box, dict):
        return
    body = _first(box, ("answer", "result", "snippet", "title"))
    if not body:
        return
    label = box.get("type") or "answer"
    console.print(
        Panel(escape(body), title=f"[accent]{escape(str(label))}[/accent]", border_style="info", expand=False)
    )


def render_knowledge_graph(payload):
    graph = payload.get("knowledge_graph")
    if not isinstance(graph, dict):
        return
    title = _first(graph, ("title",))
    if not title:
        return
    subtitle = _first(graph, ("type", "entity_type"))
    description = _first(graph, ("description",))
    head = f"[bold]{escape(title)}[/bold]"
    if subtitle:
        head += f"  [dim]{escape(subtitle)}[/dim]"
    body = head + (f"\n{escape(description)}" if description else "")
    console.print(Panel(body, border_style="accent", expand=False))


def render_results(payload, result_key, limit=None):
    items = payload.get(result_key) or []
    if limit:
        items = items[:limit]
    if not items:
        return 0
    for index, item in enumerate(items, start=1):
        title = escape(item_title(item))
        console.print(f"[accent]{index:>2}.[/accent] [bold]{title}[/bold]")
        link = item_link(item)
        if link:
            console.print(f"    [info]{escape(link)}[/info]")
        meta = item_meta(item)
        if meta:
            console.print(f"    [dim]{escape(meta)}[/dim]")
        snippet = item_snippet(item)
        if snippet:
            console.print(f"    {escape(snippet)}", highlight=False)
        console.print()
    return len(items)


def render_related_questions(payload, limit=5):
    questions = payload.get("related_questions")
    if not isinstance(questions, list) or not questions:
        return
    console.print("[step]People also ask[/step]")
    for item in questions[:limit]:
        text = _first(item, ("question", "title")) if isinstance(item, dict) else str(item)
        if text:
            console.print(f"  [dim]-[/dim] {escape(text)}")
    console.print()


def summary_line(payload, shown, transport):
    meta = payload.get("search_metadata") or {}
    info = payload.get("search_information") or {}
    parts = [f"{shown} results shown"]
    total = info.get("total_results")
    if isinstance(total, (int, float)):
        parts.append(f"{int(total):,} total")
    if meta.get("total_time_taken"):
        parts.append(f"{meta['total_time_taken']}s")
    parts.append(f"via {transport}")
    return " · ".join(parts)


def render_account(payload):
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="info", no_wrap=True)
    table.add_column()
    rows = (
        ("Account", payload.get("account_email") or payload.get("account_id")),
        ("Plan", payload.get("plan_name")),
        ("Searches / month", payload.get("searches_per_month")),
        ("Used this month", payload.get("this_month_usage")),
        ("Left this month", payload.get("plan_searches_left")),
        ("Used last hour", payload.get("this_hour_searches")),
        ("Extra credits left", payload.get("extra_credits")),
    )
    for label, value in rows:
        if value not in (None, ""):
            table.add_row(label, escape(str(value)))
    console.print(table)


def render_locations(entries, limit=10):
    table = Table(show_header=True, header_style="accent", box=None, pad_edge=False)
    table.add_column("CANONICAL NAME", overflow="fold")
    table.add_column("TARGET TYPE")
    table.add_column("REACH", justify="right")
    for entry in entries[:limit]:
        if not isinstance(entry, dict):
            continue
        reach = entry.get("reach")
        table.add_row(
            escape(str(entry.get("canonical_name") or entry.get("name") or "")),
            escape(str(entry.get("target_type") or "")),
            f"{int(reach):,}" if isinstance(reach, (int, float)) else "",
        )
    console.print(table)
