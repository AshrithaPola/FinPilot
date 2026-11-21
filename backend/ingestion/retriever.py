"""The hybrid store as a LangChain retriever.

`StoreRetriever(store=store.scoped(principal), filters=Filters(...))` runs
the store's own search (dense, full-text and question legs under the
caller's row-level security, fused, reranked) and returns LangChain
Documents: each chunk's text as page_content, every other field of its
RetrievedChunk as metadata, so `chunk_of` gives the chunk back unchanged.
The executor searches through it, so LangChain callbacks (Langfuse) see
each search as a retriever run.
"""
import asyncio
import dataclasses
from typing import Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from ingestion.store import RetrievedChunk


class StoreRetriever(BaseRetriever):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    store: Any  # a store scoped to the caller (HybridStore.scoped), or anything with its search()
    filters: Any = None  # ingestion.store.Filters

    async def _aget_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        hits = await self.store.search(query, filters=self.filters)
        return [Document(page_content=h.text, metadata={k: v for k, v in dataclasses.asdict(h).items() if k != "text"})
                for h in hits]

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        # shortcut: the store's search is async; a sync caller gets its own event loop.
        return asyncio.run(self._aget_relevant_documents(query, run_manager=run_manager))


def chunk_of(document: Document) -> RetrievedChunk:
    return RetrievedChunk(text=document.page_content, **document.metadata)
