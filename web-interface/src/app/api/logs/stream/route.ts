import { getChannelSummaries, getLogs, subscribe } from "@/lib/log-store";
import type { LogBatch } from "@/lib/types";

export async function GET() {
  const encoder = new TextEncoder();
  let unsubscribe = () => {};
  let heartbeat: ReturnType<typeof setInterval> | null = null;

  const stream = new ReadableStream({
    start(controller) {
      const send = (event: string, data: unknown, id?: string) => {
        const payload = typeof data === "string" ? data : JSON.stringify(data);
        let chunk = `event: ${event}\n`;
        if (id) chunk += `id: ${id}\n`;
        chunk += `data: ${payload}\n\n`;
        controller.enqueue(encoder.encode(chunk));
      };

      send("snapshot", {
        count: getLogs().length,
        channels: getChannelSummaries(),
        logs: getLogs(),
      });

      unsubscribe = subscribe((batch: LogBatch) => {
        send("log-batch", batch, batch.batch_id);
      });

      heartbeat = setInterval(() => {
        controller.enqueue(encoder.encode(": keepalive\n\n"));
      }, 15000);
    },
    cancel() {
      unsubscribe();
      if (heartbeat) clearInterval(heartbeat);
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
