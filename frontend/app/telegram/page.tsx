import Script from "next/script";
import { TelegramTop } from "@/components/telegram-top";
import { ReplyAssistant } from "@/components/reply-assistant";

export default async function TelegramPage({searchParams}: {searchParams: Promise<{view?: string}>}) {
  const assistant = (await searchParams).view === "assistant";
  return <><Script src="https://telegram.org/js/telegram-web-app.js" strategy="beforeInteractive"/>
    <nav className="mini-nav" aria-label="Разделы мини-приложения">
      <a href="/telegram" aria-current={!assistant ? "page" : undefined}>Вакансии</a>
      <a href="/telegram?view=assistant" aria-current={assistant ? "page" : undefined}>Помощник</a>
    </nav>
    {assistant ? <main className="mini-assistant"><h1>Помощник</h1><ReplyAssistant telegram/></main> : <TelegramTop/>}</>;
}
