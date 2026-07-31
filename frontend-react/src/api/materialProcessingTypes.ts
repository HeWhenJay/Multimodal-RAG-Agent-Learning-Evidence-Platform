// 工作台资料处理快照类型，单独维护以兼容其他 Agent 类型的并行开发改动。
export interface MaterialProcessingPhase {
  phaseCode: 'UPLOAD' | 'PARSE' | 'CHUNK' | 'EMBEDDING' | 'INDEX' | 'READY' | string;
  phaseLabel: string;
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | string;
  message?: string | null;
  updatedAt?: string | null;
}

export interface MaterialProcessingProgress {
  materialStatus: string;
  statusLabel: string;
  isProcessing: boolean;
  isTerminal: boolean;
  currentPhaseCode: 'UPLOAD' | 'PARSE' | 'CHUNK' | 'EMBEDDING' | 'INDEX' | 'READY' | string;
  currentPhaseLabel: string;
  currentStageCode: string;
  currentStageLabel: string;
  message: string;
  detail?: string | null;
  percent: number;
  currentStep?: number | null;
  totalSteps?: number | null;
  currentChunk?: number | null;
  totalChunks?: number | null;
  completedPhaseCount: number;
  totalPhaseCount: number;
  startedAt?: string | null;
  lastUpdatedAt?: string | null;
  elapsedSeconds: number;
  failureMessage?: string | null;
  nextAction: string;
  phases: MaterialProcessingPhase[];
}

declare module './types' {
  interface LearningMaterial {
    processingProgress?: MaterialProcessingProgress | null;
  }
}
