export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const file = body instanceof FormData;
  const response = await fetch(`/api/backend/${path}`, {
    method,
    headers: body && !file ? {"Content-Type": "application/json"} : undefined,
    body: body ? file ? body : JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail :
      response.status === 422 ? "Проверьте значения и длину полей." : "Не удалось выполнить запрос.");
  }
  return response.json();
}
