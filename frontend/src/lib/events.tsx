import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { z } from "zod";
import { eventSchema, type ServerEvent } from "./schema";

type Listener = (event: ServerEvent, id: string) => void;
const listeners = new Set<Listener>();
const Status = createContext("连接中");
export function useConnection() {
  return useContext(Status);
}
export function useEvents(listener: Listener) {
  useEffect(() => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, [listener]);
}
export function EventsProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState("连接中");
  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onopen = () => setStatus("已连接");
    source.onerror = () => setStatus("连接中断，正在重连");
    for (const option of eventSchema.options) {
      const name = option.shape.type.value;
      source.addEventListener(name, (event: MessageEvent<string>) => {
        try {
          const raw: unknown = JSON.parse(event.data);
          const object = z.record(z.string(), z.unknown()).parse(raw);
          const parsed = eventSchema.parse({ ...object, type: name });
          for (const listener of listeners) listener(parsed, event.lastEventId);
        } catch {
          setStatus("事件格式异常，请刷新页面");
        }
      });
    }
    return () => source.close();
  }, []);
  return <Status.Provider value={status}>{children}</Status.Provider>;
}
