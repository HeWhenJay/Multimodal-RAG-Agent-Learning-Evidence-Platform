import React from 'react';
import ReactDOM from 'react-dom/client';
import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom';
import { AppLayout } from './shell/AppLayout';
import { AgentTasks } from './pages/AgentTasks';
import { Dashboard } from './pages/Dashboard';
import { JdAnalysis } from './pages/JdAnalysis';
import { KnowledgeBase } from './pages/KnowledgeBase';
import { LearningMaterials } from './pages/LearningMaterials';
import { ResumeAdaptation } from './pages/ResumeAdaptation';
import { Settings } from './pages/Settings';
import { VideoReview } from './pages/VideoReview';
import './styles.css';

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
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
    <RouterProvider router={router} future={{ v7_startTransition: true }} />
  </React.StrictMode>
);
