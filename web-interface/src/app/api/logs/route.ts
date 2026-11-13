import { getChannelSummaries, getLogs } from "@/lib/log-store";

export async function GET() {
  const logs = getLogs();
  return Response.json({
    count: logs.length,
    channels: getChannelSummaries(),
    logs,
  });
}
