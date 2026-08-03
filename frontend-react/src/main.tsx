import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';
import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom';
import { AgentWorkspace } from './pages/agent/AgentWorkspace';
import { Dashboard } from './pages/Dashboard';
import { LearningMaterials } from './pages/materials/LearningMaterials';
import { MaterialPreview } from './pages/material-preview/MaterialPreview';
import { Settings } from './pages/Settings';
import { VideoReview } from './pages/video-review/VideoReview';
import { ReviewCenter } from './pages/reviews/ReviewCenter';
import { ReviewFolderDetail } from './pages/reviews/ReviewFolderDetail';
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
      { path: 'preview/material/:id', element: <MaterialPreview /> },
      { path: 'videos', element: <VideoReview /> },
      { path: 'reviews', element: <ReviewCenter /> },
      { path: 'reviews/folders/:folderId', element: <ReviewFolderDetail /> },
      { path: 'agent', element: <AgentWorkspace /> },
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
