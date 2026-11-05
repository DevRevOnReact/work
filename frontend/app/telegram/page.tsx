import Script from "next/script";
import { TelegramTop } from "@/components/telegram-top";

export default function TelegramPage() {
  return <><Script src="https://telegram.org/js/telegram-web-app.js" strategy="beforeInteractive"/><TelegramTop/></>;
}
