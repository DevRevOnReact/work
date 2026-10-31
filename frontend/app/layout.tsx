import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "JobPilot · Поиск работы с опорой на ваш опыт",
  description: "Вакансии, резюме и отклики в личном пространстве поиска работы",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
