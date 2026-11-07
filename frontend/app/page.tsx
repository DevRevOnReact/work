import { WorkspaceApp } from "@/components/workspace";
import type { PageName } from "@/lib/types";

export default async function Page({searchParams}: {searchParams: Promise<{view?: string; application?: string}>}) {
  const {view, application} = await searchParams;
  const initialPage = ["companies", "resumes"].includes(view || "") ? view as PageName : "search";
  const initialApplication = typeof application === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(application) ? application : null;
  return <WorkspaceApp key={`${initialPage}-${initialApplication}`} initialPage={initialPage} initialApplication={initialApplication}/>;
}
