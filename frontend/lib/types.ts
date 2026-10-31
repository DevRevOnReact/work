export type Filters = {
  title: string; keywords: string; exclude: string; skills: string; company: string;
  location: string; salary_text: string; employment_type: string; seniority: string;
  work_mode: string; salary: string; provider: string; eligibility: string;
  min_score: number; posted_days: number; skills_mode: string; keywords_mode: string; sort: string;
};
export type JobResult = {
  title: string; company: string; location: string; salary: string | null; provider: string;
  canonical_url: string; published_at: string | null; score: number; eligibility: string;
  description: string; explanation: {criteria?: {key: string; label: string; score: number; reason: string}[]};
};
export type SavedSearch = {
  id: string; name: string; resume_id: string; enabled: boolean; status: string;
  started_at: string | null; completed_at: string | null; filters: Filters;
  result_count: number; errors: string[];
};
export type SearchDetail = SavedSearch & {results: JobResult[]};
export type SearchInput = Pick<SavedSearch, "name" | "resume_id" | "enabled" | "filters">;
export type Resume = {id: string; filename: string; variant: string; text_length: number; parse_warnings: string[]};
export type Source = {id: string; company: string; provider: string; board: string; enabled: boolean; error: string | null};
export type Lead = {id: string; name: string; company: string; location: string; profile_url: string; notes: string};
export type Company = {
  id: string; name: string; careers_url: string; sources: Source[]; contacts: Lead[];
  signals: {kind: string; url: string; note: string; location: string; checked_at: string; confidence: string; rank?: number}[];
};
export type PageName = "search" | "companies" | "sources" | "resumes";
