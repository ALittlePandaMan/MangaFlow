import type {MaskPatch} from './maskBitmap'
export interface MaskSaveState {pending: boolean, blocked: boolean, error: unknown}

/** Immutable, ordered checkpoints: every finished stroke is one server edit. */
export class MaskAutosaveQueue {
  private frames: Array<{patch:MaskPatch,apply?:() => Promise<void>}> = []
  private bytes = 0
  private running: Promise<void> | null = null
  private failure: unknown = null
  constructor(private persist: (patch: MaskPatch) => Promise<void>,
    private saved: (patch: MaskPatch) => void, private changed: (state: MaskSaveState) => void, private maxBytes=64_000_000) {}
  get pending() {return this.frames.length > 0}
  // Actual edit bytes, not the size of the page: small strokes on a huge page
  // must not freeze painting behind two full-frame network uploads.
  get blocked() {return this.bytes >= this.maxBytes || this.frames.length >= 1024}
  private publish() {this.changed({pending:this.pending,blocked:this.blocked,error:this.failure})}

  enqueue(patch: MaskPatch, apply?:() => Promise<void>) {
    this.frames.push({patch:{...patch,pixels:patch.pixels.slice()},apply})
    this.bytes += patch.pixels.byteLength
    this.publish()
    if (!this.failure) void this.start().catch(() => {})
  }

  private start(): Promise<void> {
    if (this.running) return this.running
    this.failure = null
    const task = (async () => {
      try {
        while (this.frames.length) {
          const frame = this.frames[0]
          if (frame.apply) await frame.apply()
          else await this.persist(frame.patch)
          this.frames.shift()
          this.bytes -= frame.patch.pixels.byteLength
          this.saved(frame.patch)
          this.publish()
        }
      } catch (reason) {this.failure=reason; throw reason}
    })()
    this.running = task
    const finish = () => {if (this.running === task) this.running=null; this.publish()}
    void task.then(finish,finish)
    this.publish()
    return task
  }

  /** Also retries a retained failed checkpoint; never silently drops pixels. */
  async flush() {
    while (this.frames.length || this.running) await (this.running || this.start())
  }
}
