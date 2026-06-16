import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';
import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom';
import { AgentTasks } from './pages/AgentTasks';
import { Dashboard } from './pages/Dashboard';
import { JdAnalysis } from './pages/JdAnalysis';
import { KnowledgeBase } from './pages/KnowledgeBase';
import { LearningMaterials } from './pages/LearningMaterials';
import { ResumeAdaptation } from './pages/ResumeAdaptation';
import { Settings } from './pages/Settings';
import { VideoReview } from './pages/VideoReview';
import { RequireAuth } from './routes/RequireAuth';
import { AppLayout } from './shell/AppLayout';
import { AuthProvider } from './stores/auth';
import './styles.css';

const Login = lazy(() => import('./pages/Login').then((module) => ({ default: module.Login })));

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
