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

export interface MaterialPreview {
  materialId: number;
  title: string;
  documentType: string;
  source?: string | null;
  contentType?: string | null;
  content: string;
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
  metadata?: Record<string, unknown>;
}

export interface RagQueryPayload {
  question: string;
  topK?: number;
  candidateMultiplier?: number;
  metadataFilter?: Record<string, unknown>;
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
  currentFilePath?: string | null;
  currentPublicUrl?: string | null;
  fileType: string;
  fields: ResumeTemplateFieldBinding[];
  unsupportedRegions: string[];
  layoutFingerprint?: Record<string, unknown>;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface ResumeTemplatePreviewRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ResumeTemplatePreviewPage {
  pageIndex: number;
  width: number;
  height: number;
  imageUrl: string;
}

export interface ResumeTemplateRegionAnnotation {
  annotationId?: string | null;
  fieldId?: string | null;
  pageIndex: number;
  rect: ResumeTemplatePreviewRect;
  sourceType: 'AUTO' | 'MANUAL_BOUND' | 'MANUAL_UNBOUND' | string;
  editable: boolean;
  sectionKey: string;
  userInstruction?: string | null;
  requiredEvidencePolicy: 'NONE' | 'OPTIONAL' | 'REQUIRED' | string;
  status: 'ACTIVE' | 'IGNORED' | string;
  annotationRevision?: number | null;
}

export interface ResumeTemplatePreview {
  templateId: string;
  version: number;
  previewStatus: 'READY' | 'PARTIAL' | 'UNAVAILABLE' | string;
  pages: ResumeTemplatePreviewPage[];
  annotations: ResumeTemplateRegionAnnotation[];
  unmappedFields: Record<string, unknown>[];
  warnings: string[];
  generatedAt?: string | null;
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
  allowedFieldIds?: string[];
  annotationRevision?: number | null;
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

export interface AgentTaskInput {
  goal: string;
  question?: string;
  workspaceMode?: string;
  jobDescription?: string;
  resumeText?: string;
  topK?: number;
  candidateMultiplier?: number;
  saveDraft?: boolean;
  enableWebSearch?: boolean;
  webSearchQuery?: string;
  webSearchMaxResults?: number;
  resumeMaterialId?: number;
  resumeMaterialTitle?: string;
  resumeTemplateId?: string;
  resumeTemplatePath?: string;
  resumeTemplateOutputDir?: string;
  toolHints?: string[];
  metadataFilter?: Record<string, unknown>;
}

export interface AgentTaskCreatePayload {
  taskType: 'pure_read_query' | 'planning_task' | 'mutation_task';
  title?: string;
  input: AgentTaskInput;
}

export interface AgentReviewDecisionPayload {
  decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED';
  comment?: string;
  changes?: Record<string, unknown>;
}

export interface AgentOperationUndoPayload {
  idempotencyKey: string;
  reason?: string;
}

export interface AgentToolCall {
  id: string;
  taskId?: string;
  toolName: string;
  toolType: 'READ' | 'MUTATION' | 'SYSTEM' | string;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'REJECTED' | string;
  request?: Record<string, unknown>;
  response?: Record<string, unknown>;
  ownershipVerified?: boolean | null;
  scope?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentTask {
  id: string;
  taskType: string;
  status: 'CREATED' | 'RUNNING' | 'WAITING_TOOL_RESULT' | 'COMPLETED' | 'CANCELED' | 'FAILED' | string;
  title: string;
  input: Record<string, unknown>;
  plan?: Record<string, unknown>;
  draft?: Record<string, unknown>;
  final?: Record<string, unknown>;
  pythonThreadId?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  toolCalls?: AgentToolCall[];
  reviews?: AgentHumanReview[];
  operations?: AgentOperation[];
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentHumanReview {
  id: string;
  taskId?: string;
  reviewType: 'PLAN' | 'OUTPUT' | 'CRUD' | string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED' | 'EXPIRED' | string;
  proposal?: Record<string, unknown>;
  decision?: Record<string, unknown>;
  reviewedBy?: string | null;
  reviewedAt?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  expiresAt?: string | null;
}

export interface AgentToolDefinition {
  toolName: string;
  toolType: string;
  requiresReview: boolean;
  approvalType?: string | null;
  stage: number;
  description: string;
}

export interface AgentOperation {
  id: string;
  taskId?: string;
  reviewId?: string | null;
  operationType: string;
  resourceType: string;
  resourceId: string;
  status: 'PENDING_APPROVAL' | 'APPLIED_UNDOABLE' | 'UNDONE' | 'UNDO_EXPIRED' | 'FINALIZED' | 'FAILED' | string;
  beforeSnapshotRef?: string | null;
  afterSnapshotRef?: string | null;
  idempotencyKey: string;
  undoDeadline?: string | null;
  auditEventId?: number | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentMemory {
  id: string;
  userId?: string | null;
  memoryType: string;
  namespace: string;
  scopeType: string;
  scopeId?: string | null;
  subjectKey: string;
  content: string;
  summary: string;
  evidenceRefs?: Record<string, unknown>[];
  sourceTaskId?: string | null;
  sourceToolCallId?: string | null;
  sourceReviewId?: string | null;
  status: 'PENDING_REVIEW' | 'PENDING_INDEX' | 'ACTIVE' | 'INDEX_FAILED' | 'ARCHIVED' | 'SUPERSEDED' | 'REJECTED' | 'DELETED' | string;
  confidence?: number | null;
  importance?: number | null;
  sensitivityLevel?: string | null;
  consentSource?: string | null;
  accessCount?: number | null;
  lastAccessedAt?: string | null;
  validFrom?: string | null;
  validUntil?: string | null;
  deletedAt?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentMemoryCreatePayload {
  memoryType: string;
  namespace: string;
  scopeType: string;
  scopeId?: string | null;
  subjectKey: string;
  content: string;
  summary?: string | null;
  evidenceRefs?: Record<string, unknown>[];
  importance?: number | null;
}
