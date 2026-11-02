export type Filters = {
  title: string; keywords: string; exclude: string; skills: string; company: string;
  location: string; salary_text: string; employment_type: string; seniority: string;
  work_mode: string; salary: string; provider: string; eligibility: string;
  min_score: number; posted_days: number; skills_mode: string; keywords_mode: string; sort: string;
};
export type JobResult = {
  title: string; company: string; location: string; salary: string | null; provider: string;
  canonical_url: string; published_at: string | null; score: number; eligibility: string;
  description: string; explanation: {blockers?: string[]; criteria?: {key: string; label: string; score: number; max_score?: number; reason: string}[]};
};
export type SavedSearch = {
  id: string; name: string; resume_id: string; enabled: boolean; status: string;
  started_at: string | null; completed_at: string | null; filters: Filters;
  result_count: number; errors: string[];
  progress?: SearchProgress; next_run_at?: string | null; schedule_timezone?: string;
};
export type SearchProgress = {
  stage?: string; message?: string; updated_at?: string; done?: number; total?: number;
  fetched?: number; fresh?: number; failures?: number;
  events?: {at: string; message: string; level: string}[];
};
export type CompanyDiscovery = {
  id: string; country: string; page: number; status: string; progress: SearchProgress;
  started_at: string; completed_at: string | null;
  result: {found?: number; added?: number; errors?: string[]; next_page?: number | null};
};
export type SearchDetail = SavedSearch & {results: JobResult[]};
export type SearchInput = Pick<SavedSearch, "name" | "resume_id" | "enabled" | "filters">;
export type Resume = {id: string; filename: string; variant: string; text_length: number; parse_warnings: string[]};
export type Source = {id: string; company: string; provider: string; board: string; enabled: boolean; error: string | null};
export type Lead = {id: string; name: string; company: string; location: string; profile_url: string; notes: string};
export type Company = {
  id: string; name: string; careers_url: string; sources: Source[]; contacts: Lead[];
  signals: {kind: string; url: string; note: string; location: string; country?: string; checked_at: string; confidence: string; rank?: number}[];
};
export type TopVacancy = Pick<JobResult, "title" | "company" | "location" | "salary" | "provider" | "canonical_url" | "published_at" | "score" | "eligibility"> & {
  id: string; resume_filename: string; search_name: string;
};
export type TopVacancies = {window_hours: number; min_score: number; total: number; items: TopVacancy[]};
export type PageName = "search" | "top" | "companies" | "sources" | "resumes";
export type AutomationSettings = {
  enabled: boolean; mode: "manual" | "assisted" | "autonomous"; auto_apply: boolean;
  min_score: number; daily_limit: number; authorized_domains: string[]; excluded_companies: string[];
  required_approval_categories: string[]; supported_domains: string[]; browser_available: boolean; browser_headless: boolean;
};
export type ApplicationDetail = {
  id: string; job_id: string; state: string; attention: string | null; mode: string;
  progress: {step?: string; message?: string; question?: string; questions?: string[]; browser_status?: string; blocked_host?: string; resume_filename?: string;
    events?: {at?: string; message: string; level?: string; step?: string}[]};
  resume_id: string | null; generated_resume_url: string | null; application_url?: string | null;
  answers: {question: string; answer: string; evidence?: unknown}[];
  job: {company: string; title: string; canonical_url: string};
  submit_intent_at: string | null; submitted_at: string | null;
  approval_token: string | null; screenshot_url: string | null;
};
