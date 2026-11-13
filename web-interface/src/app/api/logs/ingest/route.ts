import { ingestBatch, parseSseBody } from "@/lib/log-store";

export async function POST(request: Request) {
  const contentType = request.headers.get("content-type") || "";
  const raw = await request.text();

  let batch = null;
  if (contentType.includes("text/event-stream") || raw.includes("event:")) {
    batch = parseSseBody(raw);
  } else {
    try {
      batch = JSON.parse(raw);
    } catch {
      return Response.json({ error: "invalid body" }, { status: 400 });
    }
  }

  if (!batch) {
    return Response.json({ error: "could not parse log batch" }, { status: 400 });
  }

  const result = ingestBatch(batch);
  if (!result.accepted) {
    return Response.json({ error: "batch rejected" }, { status: 400 });
  }
  return Response.json({ ok: true, ingested: result.count, batch_id: batch.batch_id }, { status: 202 });
}
