"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { busy } from "@/lib/search-filters";
import type { PageName, Resume, SavedSearch, SearchDetail } from "@/lib/types";

export function useResource<T>(path: string, enabled = true) {
  return useQuery<T>({queryKey: [path], queryFn: () => api<T>(path), enabled, staleTime: 60_000, retry: false});
}

export function useWorkspace() {
  const query = useQueryClient();
  const [page, setPage] = useState<PageName>("search");
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [sourceCompany, setSourceCompany] = useState("");
  const searches = useQuery<SavedSearch[]>({
    queryKey: ["searches"], queryFn: () => api("searches"), retry: false,
    refetchInterval: state => page === "search" ? state.state.data?.some(s => busy(s.status)) ? 2000 : 15000 : false,
  });
  const resumes = useResource<Resume[]>("resumes");
  const current = (selected ? searches.data?.find(s => s.id === selected) : !editing ? searches.data?.[0] : null) || null;
  const detail = useQuery<SearchDetail>({
    queryKey: [`searches/${current?.id}`, current?.completed_at, current?.status], queryFn: () => api(`searches/${current?.id}`),
    enabled: Boolean(current && !editing), retry: false,
    // The summaries change at completion; descriptions are fetched only for the selected search.
    staleTime: 30_000,
  });

  async function action(fn: () => Promise<unknown>, success: string) {
    setPending(true); setError(""); setMessage("");
    try {
      await fn();
      await query.invalidateQueries();
      setMessage(success);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка запроса");
      return false;
    } finally { setPending(false); }
  }
  function navigate(next: PageName) { setPage(next); setMessage(""); setError(""); }
  return {page, navigate, selected, setSelected, editing, setEditing, message, error, pending,
    searches, resumes, current, detail, action, sourceCompany, setSourceCompany};
}
export type WorkspaceState = ReturnType<typeof useWorkspace>;
