export interface Result<T> {
  code: number;
  msg: string | null;
  data: T;
}

export interface AuthUser {
  id: number;
  account: string;
  displayName: string;
  email: string | null;
  role: string;
  loginAt?: string | null;
}

export interface AuthLoginResult {
  token: string;
  expiresAt: string;
  user: AuthUser;
}

export interface RagOverview {
  materialCount: number;
  chunkCount: number;
  evidenceCount: number;
  lastIndexedTitle?: string | null;
}

export interface RagProgress {
  stageCode: string;
  stageLabel?: string | null;
  message: string;
  status?: string | null;
  currentStep?: number | null;
  totalSteps?: number | null;
  currentChunk?: number | null;
  totalChunks?: number | null;
  chunkId?: string | null;
  blockId?: string | null;
  percent?: number | null;
  detail?: string | null;
  createdAt?: string | null;
}

export interface LearningMaterial {
  id: number;
  title: string;
  userId?: string | null;
  documentType: string;
  source: string;
  status: string;
  parser?: string | null;
  documentSummary?: string | null;
  chunkCount: number;
  originalFilename?: string | null;
  originalFilePath?: string | null;
  storageType?: string | null;
  objectKey?: string | null;
  publicUrl?: string | null;
  latestProgress?: RagProgress | null;
  progressEvents?: RagProgress[];
  createdAt?: string;
  updatedAt?: string;
}

export interface MaterialUploadChunk {
  uploadId: string;
  filename: string;
  chunkIndex: number;
  totalChunks: number;
  receivedChunks: number;
  nextChunkIndex?: number | null;
  status?: 'UPLOADING' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | string;
  message?: string | null;
  completed: boolean;
  material: LearningMaterial | null;
}

export interface RagEvidence {
  evidenceId: string;
  documentId: string;
  documentTitle?: string | null;
  blockId?: string | null;
  blockType?: string | null;
  pageIndex?: number | null;
  slideIndex?: number | null;
  startTime?: string | null;
  endTime?: string | null;
  sheetName?: string | null;
  cellRange?: string | null;
  sectionTitle?: string | null;
  title: string;
  snippet: string;
  source: string;
  sourcePath?: string | null;
  assetPath?: string | null;
  playbackUrl?: string | null;
  sectionName: string;
  documentType: string;
  score: number;
  retrievalSource?: string | null;
  parseEngine?: string | null;
}

export interface RagQueryResult {
  answer: string;
  expandedQueries: string[];
  evidences: RagEvidence[];
  diagnostics?: Record<string, unknown>;
  progressEvents?: RagProgress[];
}

export interface RagQueryTask {
  taskId: string;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED' | 'EXPIRED' | string;
  message: string;
  progressEvents?: RagProgress[];
  result?: RagQueryResult | null;
  errorMessage?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface RagQueryHistory {
  id: number;
  taskId?: string | null;
  question: string;
  answer?: string | null;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED' | 'EXPIRED' | string;
  topK: number;
  evidenceCount: number;
  expandedQueries: string[];
  evidences: RagEvidence[];
  diagnostics?: Record<string, unknown>;
  progressEvents?: RagProgress[];
  errorMessage?: string | null;
  durationMs?: number | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface ResumeTemplateFieldBinding {
  templateId: string;
  version: number;
  fieldId: string;
  sectionKey: string;
  displayName: string;
  sourceText: string;
  sourceTextHash: string;
  locationRefs?: Record<string, unknown>[];
  styleFingerprint?: Record<string, unknown>;
  maxChars: number;
  maxLines: number;
  requiredEvidencePolicy: string;
  unsupportedRegions: string[];
}

export interface ResumeTemplate {
  templateId: string;
  version: number;
  status: string;
  filename: string;
  fileType: string;
  fields: ResumeTemplateFieldBinding[];
  unsupportedRegions: string[];
  layoutFingerprint?: Record<string, unknown>;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface ResumeContentPatch {
  fieldId: string;
  sourceTextHash: string;
  newText: string;
  rewriteReason: string;
  evidenceIds: string[];
  confidence: number;
  riskFlags: string[];
  status: 'DRAFT' | 'VALIDATED' | 'CONFIRMED' | 'REJECTED' | 'EXPORTED' | string;
}

export interface ResumePatchEvidence {
  evidenceId: string;
  documentTitle?: string | null;
  sectionName?: string | null;
  snippet?: string | null;
  source?: string | null;
  score?: number | null;
}

export interface ResumePatchDraft {
  patchDraftId: string;
  templateId: string;
  version: number;
  status: string;
  provider?: string | null;
  patches: ResumeContentPatch[];
  evidenceCandidates: ResumePatchEvidence[];
  validationErrors: string[];
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface ResumeTemplateExport {
  exportId: string;
  templateId: string;
  baseVersion: number;
  exportVersion: number;
  patchDraftId: string;
  filename: string;
  filePath: string;
  storageType: string;
  publicUrl?: string | null;
  status: string;
  layoutValidation?: Record<string, unknown>;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface VideoSlice {
  id: number;
  title: string;
  topic: string;
  startTime: string;
  endTime: string;
  status: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface ResumeEvidenceAlignment {
  id: number;
  userId?: string | null;
  requirement: string;
  evidence: string;
  status: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface JdAnalysisSkill {
  id: number;
  skillName: string;
  status: string;
}

export interface JdLearningPlanItem {
  id: number;
  stepNo: number;
  title: string;
  description: string;
}

export interface JdAnalysis {
  id: number;
  userId?: string | null;
  jobDescription: string;
  matchScore: number;
  masteredPercent: number;
  partialPercent: number;
  gapPercent: number;
  skills: JdAnalysisSkill[];
  learningPlan: JdLearningPlanItem[];
  updatedAt?: string;
}

export interface JdAnalysisRequest {
  jobDescription: string;
  resumeText?: string;
}

export interface SystemSetting {
  key: string;
  group: string;
  label: string;
  value: string;
  sortOrder: number;
}

export interface DashboardData {
  materialCount: number;
  materialDelta7Days: number;
  videoSliceCount: number;
  videoSliceDelta7Days: number;
  evidenceCount: number;
  openErrorCount: number;
  errorCount30Days: number;
  recentTaskStartDate?: string | null;
  recentTaskEndDate?: string | null;
  recentTaskLimit?: number | null;
  recentMaterials: LearningMaterial[];
  recentVideoSlices: VideoSlice[];
  latestJdAnalysis: JdAnalysis | null;
  resumeAlignments: ResumeEvidenceAlignment[];
}
