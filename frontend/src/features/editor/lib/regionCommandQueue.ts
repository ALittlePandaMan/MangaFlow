import type {EditorHistoryState} from '../../../types'
import type {RegionHistoryPreview} from './regionHistory'

export interface RegionCommand extends RegionHistoryPreview {
  label:string
  entryId?:number | null
  noOp?:boolean
}

/** Serialize persistence; callers choose when to block, history stays server-side. */
export class RegionCommandQueue {
  private tail:Promise<unknown> = Promise.resolve()
  private count=0
  private failure:unknown=null
  private lastCommand:RegionCommand | undefined
  constructor(private changed:()=>void) {}

  get pending() {return this.count>0}
  get failed() {return this.failure !== null}
  get last() {return this.lastCommand}
  record(step:RegionCommand) {
    this.lastCommand=step
  }
  remove(step:RegionCommand) {
    if (this.lastCommand===step) this.lastCommand=undefined
  }
  /** Called only after a barrier/global command, never on intermediate acks. */
  observe(state:EditorHistoryState) {
    if (this.pending) return
    if (this.lastCommand?.entryId != null && this.lastCommand.entryId!==state.undo_id) this.lastCommand=undefined
  }
  reset() {this.lastCommand=undefined;this.failure=null;this.tail=Promise.resolve();this.changed()}

  enqueue<T>(run:()=>Promise<T>):Promise<T> {
    this.count++;this.changed()
    // A failure invalidates subsequent dependent commands. Do not apply a
    // later PATCH against a creation/undo that never reached the server.
    const result=this.tail.catch(()=>undefined).then(async()=>{
      if (this.failure !== null) throw this.failure
      try {return await run()} catch(error) {this.failure=error;throw error}
    })
    this.tail=result
    void result.then(()=>this.finish(),()=>this.finish())
    return result
  }
  private finish() {this.count--;this.changed()}
  async flush() {
    // Include jobs appended while an earlier request was awaited.
    while(this.pending) {const tail=this.tail;await tail;if(tail===this.tail) break}
    if(this.failure !== null) throw this.failure
  }
  async settled() {while(this.pending) {const tail=this.tail;await tail.catch(()=>undefined);if(tail===this.tail) break}}
}
