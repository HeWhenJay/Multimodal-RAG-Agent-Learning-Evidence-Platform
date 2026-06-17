import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';
import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom';
import { AgentTasks } from './pages/AgentTasks';
import { Dashboard } from './pages/Dashboard';
import { JdAnalysis } from './pages/jd-analysis/JdAnalysis';
import { KnowledgeBase } from './pages/knowledge-base/KnowledgeBase';
import { LearningMaterials } from './pages/materials/LearningMaterials';
import { ResumeAdaptation } from './pages/resume-adapter/ResumeAdaptation';
import { Settings } from './pages/Settings';
import { VideoReview } from './pages/video-review/VideoReview';
import { RequireAuth } from './routes/RequireAuth';
import { AppLayout } from './layouts/AppLayout';
import { AuthProvider } from './stores/auth';
import './styles.css';

const Login = lazy(() => import('./pages/Login').then((module) => ({ default: module.Login })));

// 路由懒加载时展示统一加载态。
function RouteFallback() {
  return <div className="route-loading">正在加载...</div>;
}

const router = createBrowserRouter([
  {
    path: '/login',
    element: (
      <Suspense fallback={<RouteFallback />}>
        <Login />
      </Suspense>
    )
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'materials', element: <LearningMaterials /> },
      { path: 'knowledge', element: <KnowledgeBase /> },
      { path: 'videos', element: <VideoReview /> },
      { path: 'jd-analysis', element: <JdAnalysis /> },
      { path: 'resume', element: <ResumeAdaptation /> },
      { path: 'agent-tasks', element: <AgentTasks /> },
      { path: 'settings', element: <Settings /> },
      { path: '*', element: <Navigate to="/" replace /> }
    ]
  }
]);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </AuthProvider>
  </React.StrictMode>
);
