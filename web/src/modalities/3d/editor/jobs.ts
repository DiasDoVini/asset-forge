import { api, type Job } from "@/core/api"
import { onForgeEvent } from "@/core/events"

/** Resolves with the finished job (status done) or rejects with its error. Events first, polling as fallback. */
export function waitForJob(id: string, onProgress?: (job: Job) => void): Promise<Job> {
  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (job: Job) => {
      if (settled) return
      if (job.status === "done") {
        settled = true
        cleanup()
        resolve(job)
      } else if (job.status === "error" || job.status === "cancelled") {
        settled = true
        cleanup()
        reject(new Error(job.error ?? job.status))
      } else onProgress?.(job)
    }
    const off = onForgeEvent((e) => {
      if (e.type === "job" && e.job.id === id) finish(e.job)
    })
    const timer = window.setInterval(() => {
      api.job(id).then(finish).catch(() => undefined)
    }, 4000)
    const cleanup = () => {
      off()
      window.clearInterval(timer)
    }
  })
}

/** Run a pipeline and wait for it; returns the produced asset ref. */
export async function runJob(project: string, pipeline: string, params: Record<string, unknown>, onProgress?: (job: Job) => void,
  opts: { asset?: string } = {}): Promise<string> {
  const job = await api.submitJob({ project, pipeline, params, ...opts })
  const done = await waitForJob(job.id, onProgress)
  if (!done.result) throw new Error("job finished without a result")
  return done.result
}

export function splitRef(ref: string): { slug: string; version: number } {
  const [slug, v] = ref.split("@v")
  return { slug, version: Number(v) }
}
