"""
KnowledgeUpdater — Research paper crawler for Dozzle-Enhanced AI Log Analysis Agent.

Sources:
- ArXiv API: cs.OS, cs.DC (log analysis, anomaly detection)
- Semantic Scholar API: "log anomaly detection containers"
- GitHub Releases: amir20/dozzle, grafana/loki, vectordotdev/vector
- RSS: Grafana Loki blog, Vector.dev blog

Output: Appends top-N scored entries to SECOND-KNOWLEDGE-BRAIN.md
Schedule: Weekly (Sunday 02:00) via APScheduler
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KNOWLEDGE_BRAIN_PATH = Path(__file__).parent.parent / "SECOND-KNOWLEDGE-BRAIN.md"
TOP_N = 10
RECENCY_DAYS_MAX = 90

ARXIV_CATEGORIES = ["cs.OS", "cs.DC"]
ARXIV_KEYWORDS = [
    "log anomaly detection", "log clustering", "log parsing", "DRAIN log",
    "LogBERT", "DeepLog", "container log analysis", "log-based anomaly",
]

GITHUB_REPOS = ["amir20/dozzle", "grafana/loki", "vectordotdev/vector"]

RSS_FEEDS = [
    "https://grafana.com/blog/tag/loki/feed.xml",
    "https://vector.dev/blog/feed.xml",
]


class PaperEntry:
    def __init__(
        self, title: str, url: str, authors: str, year: int,
        abstract: str, source: str, venue: str = "",
    ):
        self.title = title
        self.url = url
        self.authors = authors
        self.year = year
        self.abstract = abstract
        self.source = source
        self.venue = venue
        self.score = 0.0

    def compute_score(self, keywords: list[str]) -> float:
        text = (self.title + " " + self.abstract).lower()
        recency = max(0, 1 - (datetime.now().year - self.year) / (RECENCY_DAYS_MAX / 365))
        relevance = sum(1 for kw in keywords if kw.lower() in text) / max(len(keywords), 1)
        self.score = 0.4 * recency + 0.6 * relevance
        return self.score

    def url_hash(self) -> str:
        return hashlib.md5(self.url.encode()).hexdigest()

    def to_markdown_row(self) -> str:
        abstract_short = self.abstract[:150] + "..." if len(self.abstract) > 150 else self.abstract
        return (
            f"| {self.title} | {self.authors} | {self.year} | {self.venue} "
            f"| {self.url} | {abstract_short} | {self.source} |"
        )


class KnowledgeUpdater:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._memory = None

    def _get_memory(self):
        if self._memory is None:
            try:
                import sys
                sys.path.insert(0, str(Path(__file__).parent.parent))
                from agent.memory.memory_manager import MemoryManager
                self._memory = MemoryManager(self.config)
            except Exception:
                pass
        return self._memory

    async def run_crawl(self, force: bool = False) -> dict:
        """Main crawl entry point. Returns summary dict."""
        logger.info("Starting knowledge update crawl...")
        all_entries: list[PaperEntry] = []

        results = await asyncio.gather(
            self._crawl_arxiv(),
            self._crawl_semantic_scholar(),
            self._crawl_github_releases(),
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, list):
                all_entries.extend(r)
            elif isinstance(r, Exception):
                logger.warning(f"Crawl source failed: {r}")

        # Score and deduplicate
        memory = self._get_memory()
        new_entries = []
        for entry in all_entries:
            entry.compute_score(ARXIV_KEYWORDS)
            if memory and memory.is_known_entry(entry.url):
                continue
            new_entries.append(entry)

        # Sort by score, take top N
        new_entries.sort(key=lambda e: e.score, reverse=True)
        top_entries = new_entries[:TOP_N]

        # Append to SECOND-KNOWLEDGE-BRAIN.md
        if top_entries:
            self._append_to_knowledge_brain(top_entries)
            if memory:
                for entry in top_entries:
                    memory.add_knowledge_entry(entry.url, entry.title, entry.source)

        next_run = self._next_sunday_0200()
        logger.info(f"Crawl complete. {len(top_entries)} new entries added. Next: {next_run}")

        return {
            "new_entries": len(top_entries),
            "total_found": len(all_entries),
            "sources": ["arxiv", "semantic_scholar", "github_releases"],
            "next_run": next_run,
        }

    async def _crawl_arxiv(self) -> list[PaperEntry]:
        """Fetch papers from ArXiv API."""
        import aiohttp
        entries = []
        query_terms = "+OR+".join(f"abs:{kw.replace(' ', '+')}" for kw in ARXIV_KEYWORDS[:3])
        cat_filter = "+OR+".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
        query = f"({query_terms})+AND+({cat_filter})"
        url = f"http://export.arxiv.org/api/query?search_query={query}&max_results=20&sortBy=submittedDate&sortOrder=descending"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    text = await resp.text()
                    entries = self._parse_arxiv_response(text)
        except Exception as e:
            logger.warning(f"ArXiv crawl failed: {e}")
        return entries

    def _parse_arxiv_response(self, xml_text: str) -> list[PaperEntry]:
        entries = []
        try:
            import xml.etree.ElementTree as ET
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(xml_text)
            for entry in root.findall("atom:entry", ns):
                title = entry.findtext("atom:title", "", ns).strip().replace("\n", " ")
                summary = entry.findtext("atom:summary", "", ns).strip().replace("\n", " ")
                arxiv_id = entry.findtext("atom:id", "", ns).strip()
                published = entry.findtext("atom:published", "", ns)[:4]
                authors = ", ".join(
                    a.findtext("atom:name", "", ns)
                    for a in entry.findall("atom:author", ns)
                )[:80]
                year = int(published) if published.isdigit() else datetime.now().year
                entries.append(PaperEntry(
                    title=title, url=arxiv_id, authors=authors,
                    year=year, abstract=summary, source="ArXiv", venue="cs.OS/cs.DC",
                ))
        except Exception as e:
            logger.warning(f"ArXiv XML parse error: {e}")
        return entries

    async def _crawl_semantic_scholar(self) -> list[PaperEntry]:
        """Fetch papers from Semantic Scholar API."""
        import aiohttp
        entries = []
        query = "docker container log anomaly detection machine learning"
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={query.replace(' ', '+')}"
            "&fields=title,abstract,year,authors,url,externalIds&limit=10"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    data = await resp.json()
                    for paper in data.get("data", []):
                        title = paper.get("title", "")
                        abstract = paper.get("abstract", "") or ""
                        year = paper.get("year") or datetime.now().year
                        authors = ", ".join(
                            a.get("name", "") for a in (paper.get("authors") or [])
                        )[:80]
                        paper_url = paper.get("url", "") or ""
                        entries.append(PaperEntry(
                            title=title, url=paper_url, authors=authors,
                            year=year, abstract=abstract, source="Semantic Scholar",
                        ))
        except Exception as e:
            logger.warning(f"Semantic Scholar crawl failed: {e}")
        return entries

    async def _crawl_github_releases(self) -> list[PaperEntry]:
        """Fetch latest GitHub releases as knowledge entries."""
        import aiohttp
        entries = []
        token = os.getenv("GITHUB_TOKEN", "")
        headers = {"Authorization": f"token {token}"} if token else {}

        for repo in GITHUB_REPOS:
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            tag = data.get("tag_name", "")
                            body = (data.get("body") or "")[:300]
                            html_url = data.get("html_url", "")
                            published = data.get("published_at", "")[:4]
                            year = int(published) if published.isdigit() else datetime.now().year
                            entries.append(PaperEntry(
                                title=f"{repo} {tag} Release",
                                url=html_url,
                                authors=repo,
                                year=year,
                                abstract=body,
                                source="GitHub Releases",
                                venue="GitHub",
                            ))
            except Exception as e:
                logger.warning(f"GitHub release crawl failed for {repo}: {e}")
        return entries

    def _append_to_knowledge_brain(self, entries: list[PaperEntry]):
        """Append new entries to SECOND-KNOWLEDGE-BRAIN.md."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        lines = [
            f"\n### {date_str} — Automated Crawl",
            f"**Sources:** ArXiv cs.OS+cs.DC, Semantic Scholar, GitHub Releases",
            f"**Entries added:** {len(entries)}",
            "",
            "| Title | Authors | Year | Venue | Link | Key Finding | Source |",
            "|-------|---------|------|-------|------|-------------|--------|",
        ]
        for entry in entries:
            lines.append(entry.to_markdown_row())

        try:
            with open(KNOWLEDGE_BRAIN_PATH, "a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(lines) + "\n")
            logger.info(f"Appended {len(entries)} entries to SECOND-KNOWLEDGE-BRAIN.md")
        except Exception as e:
            logger.error(f"Failed to write to SECOND-KNOWLEDGE-BRAIN.md: {e}")

    def _next_sunday_0200(self) -> str:
        now = datetime.now(timezone.utc)
        days_until_sunday = (6 - now.weekday()) % 7 or 7
        next_sunday = now + timedelta(days=days_until_sunday)
        next_sunday = next_sunday.replace(hour=2, minute=0, second=0, microsecond=0)
        return next_sunday.strftime("%Y-%m-%d 02:00 UTC")

    def start_scheduler(self):
        """Start APScheduler for weekly crawl at Sunday 02:00."""
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            scheduler = AsyncIOScheduler()
            scheduler.add_job(
                lambda: asyncio.create_task(self.run_crawl()),
                "cron", day_of_week="sun", hour=2, minute=0,
            )
            scheduler.start()
            logger.info("Knowledge update scheduler started (weekly Sunday 02:00)")
        except ImportError:
            logger.warning("APScheduler not installed — scheduler disabled")


if __name__ == "__main__":
    async def main():
        updater = KnowledgeUpdater()
        result = await updater.run_crawl(force=True)
        print(f"Crawl result: {result}")

    asyncio.run(main())
