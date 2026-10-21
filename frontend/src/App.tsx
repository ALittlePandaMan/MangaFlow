import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { PageLoader } from './components/LoadingUI'
import { SettingsSkeleton } from './components/SettingsSkeleton'
import { ProjectsPage } from './pages/ProjectsPage'

const EditorPage = lazy(() => import('./pages/EditorPage').then(module => ({default: module.EditorPage})))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then(module => ({default: module.SettingsPage})))

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:projectId/editor/:imageId?" element={<Suspense fallback={<PageLoader label="正在加载编辑器" detail="正在准备画布与图像编辑模块"/>}><EditorPage /></Suspense>} />
        <Route path="/settings" element={<Suspense fallback={<SettingsSkeleton/>}><SettingsPage /></Suspense>} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Route>
    </Routes>
  )
}
