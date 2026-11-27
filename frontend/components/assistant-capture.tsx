"use client";
import { useEffect, useRef, useState } from "react";
import { ImagePlus, Mic, Square } from "lucide-react";

export function AssistantCapture({onText, recognize, onError, disabled}: {
  onText: (value: string, target: "question" | "instruction") => void;
  recognize: (kind: "audio" | "image", blob: Blob) => Promise<string>;
  onError: (message: string) => void;
  disabled: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);
  const [phase, setPhase] = useState<"idle" | "asking" | "recording" | "reading">("idle");
  const [target, setTarget] = useState<"question" | "instruction">("question");

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
      if (recorder.current) {
        recorder.current.onstop = null;
        if (recorder.current.state !== "inactive") recorder.current.stop();
        recorder.current.stream.getTracks().forEach(track => track.stop());
      }
    };
  }, []);

  async function read(kind: "audio" | "image", blob: Blob, dest: typeof target) {
    setPhase("reading");
    try {
      if (blob.size > 4 * 1024 * 1024) throw new Error("Допустимый размер файла до 4 МБ.");
      const value = await recognize(kind, blob);
      if (alive.current) onText(value, dest);
    } catch (e) {
      if (alive.current) onError(e instanceof Error ? e.message : "Ошибка распознавания.");
    } finally { if (alive.current) setPhase("idle"); }
  }

  async function record() {
    setPhase("asking");
    let stream: MediaStream | undefined;
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined")
        throw new Error("Микрофон недоступен здесь. Используйте диктовку клавиатуры или голосовое сообщение боту.");
      stream = await navigator.mediaDevices.getUserMedia({audio: true});
      if (!alive.current) { stream.getTracks().forEach(t => t.stop()); return; }
      const mic = new MediaRecorder(stream);
      recorder.current = mic;
      const parts: BlobPart[] = [];
      mic.ondataavailable = e => { if (e.data.size) parts.push(e.data); };
      mic.onstop = () => {
        mic.stream.getTracks().forEach(track => track.stop());
        if (timer.current) clearTimeout(timer.current);
        recorder.current = null;
        if (alive.current) void read("audio", new Blob(parts, {type: mic.mimeType}), target);
      };
      mic.start();
      setPhase("recording");
      timer.current = setTimeout(() => { if (mic.state === "recording") mic.stop(); }, 60000);
    } catch (e) {
      stream?.getTracks().forEach(track => track.stop());
      if (alive.current) {
        onError(e instanceof DOMException ? "Браузер не открыл микрофон. Проверьте разрешение или напишите текст."
          : e instanceof Error ? e.message : "Не удалось начать запись.");
        setPhase("idle");
      }
    }
  }
  const busy = phase !== "idle" || disabled;
  return <div className="capture">
    <div className="button-row">
      <button type="button" className="button" disabled={busy && phase !== "recording"}
        onClick={() => phase === "recording" ? recorder.current?.stop() : void record()}>
        {phase === "recording" ? <Square size={16}/> : <Mic size={16}/>}
        {phase === "recording" ? "Закончить запись" : "Диктовать"}
      </button>
      <select aria-label="Куда добавить диктовку" disabled={busy} value={target}
        onChange={e => setTarget(e.target.value as typeof target)}>
        <option value="question">В сообщение</option><option value="instruction">В пожелание к ответу</option>
      </select>
      <button type="button" className="button" disabled={busy} onClick={() => input.current?.click()}>
        <ImagePlus size={16}/> Скриншот
      </button>
      <input type="file" hidden ref={input} accept="image/png,image/jpeg" onChange={e => {
        const file = e.target.files?.[0]; e.target.value = "";
        if (file) void read("image", file, "question");
      }}/>
    </div>
    {phase !== "idle" && <small role="status">{{asking:"Ждём разрешение на микрофон…",
      recording:"Идёт запись, до 60 секунд.",reading:"Распознаём текст на вашем сервере…"}[phase]}</small>}
  </div>;
}
