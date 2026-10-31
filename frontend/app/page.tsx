import { WorkspaceApp } from "@/components/workspace";
import type { PageName } from "@/lib/types";

export default async function Page({searchParams}: {searchParams: Promise<{view?: string}>}) {
  const {view} = await searchParams;
  const initialPage = ["companies", "sources", "resumes"].includes(view || "") ? view as PageName : "search";
  return <WorkspaceApp key={initialPage} initialPage={initialPage}/>;
}
