import type {ProcessingTask} from '../../../types'

export const taskFixture = (id: string, patch: Partial<ProcessingTask> = {}): ProcessingTask => ({
  id, project_id:'project', image_id:`page-${id}`, region_id:null, task_type:'full', status:'QUEUED',
  progress:0, current_stage:null, message:'Queued', error_message:null, payload:{}, pause_requested:false,
  created_at:`2026-10-18T00:00:${id.padStart(2,'0')}Z`, updated_at:'2026-10-18T01:00:00Z', ...patch,
})
