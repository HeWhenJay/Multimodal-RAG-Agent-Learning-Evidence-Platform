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
  answerStatus?: 'ANSWERED' | 'REFUSED' | string;
  refusalReason?: string | null;
  refusalPolicy?: string | null;
  confidence?: number | null;
  supportingEvidenceIds?: string[];
  refusalMessage?: string | null;
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
  answerStatus?: 'ANSWERED' | 'REFUSED' | string;
  refusalReason?: string | null;
  refusalPolicy?: string | null;
  confidence?: number | null;
  supportingEvidenceIds?: string[];
  refusalMessage?: string | null;
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
  evidenceCount: number;
  openErrorCount: number;
  errorCount30Days: number;
  recentTaskStartDate?: string | null;
  recentTaskEndDate?: string | null;
  recentTaskLimit?: number | null;
  recentMaterials: LearningMaterial[];
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
  toolHints?: string[];
  metadataFilter?: Record<string, unknown>;
}

export interface AgentTaskCreatePayload {
  taskType: 'pure_read_query' | 'planning_task' | 'mutation_task';
  folderId?: string | null;
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

export interface AgentChatMessage {
  id: string;
  taskId?: string;
  sequenceNo?: number | null;
  role: 'USER' | 'ASSISTANT' | 'SYSTEM' | 'TOOL' | string;
  messageType: 'USER_GOAL' | 'STATUS' | 'TOOL_OBSERVATION' | 'PLAN_REVIEW' | 'OUTPUT_REVIEW' | 'FINAL_ANSWER' | 'ERROR' | 'REVIEW_DECISION' | 'OPERATION_UNDO' | string;
  content: string;
  payload?: Record<string, unknown>;
  sourceEventType?: string | null;
  sourceId?: string | null;
  dedupeKey?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentMessagePage {
  taskId: string;
  messages: AgentChatMessage[];
  oldestSequenceNo?: number | null;
  newestSequenceNo?: number | null;
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
  limit: number;
}

export interface AgentStreamEvent {
  taskId: string;
  status?: string;
  eventType: string;
  pythonThreadId?: string | null;
  draft?: Record<string, unknown>;
  toolCallId?: string | null;
  toolName?: string | null;
  toolStatus?: string | null;
  reviewRequest?: Record<string, unknown> | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  createdAt?: string | null;
}

export interface AgentConversationSummary {
  id: string;
  taskId?: string;
  summaryType: string;
  coveredMessageStartId?: string | null;
  coveredMessageEndId?: string | null;
  coveredMessageCount?: number | null;
  rawTokenEstimate?: number | null;
  compressedTokenEstimate?: number | null;
  summary?: Record<string, unknown>;
  summaryText: string;
  keyFacts?: Array<Record<string, unknown>>;
  evidenceRefs?: Array<Record<string, unknown>>;
  compressionModel?: string | null;
  compressionPromptVersion?: string | null;
  compressionVersion?: number | null;
  status: string;
  diagnostics?: Record<string, unknown>;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentTask {
  id: string;
  folderId?: string | null;
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
  messages?: AgentChatMessage[];
  summaries?: AgentConversationSummary[];
  messageWindowLimit?: number | null;
  hasMoreMessagesBefore?: boolean | null;
  summaryWindowLimit?: number | null;
  hasMoreSummaries?: boolean | null;
  summaryCount?: number | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentConversationFolder {
  id: string | null;
  name: string;
  sortOrder?: number | null;
  conversationCount: number;
  conversations: AgentTask[];
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentConversationTree {
  unfiled: AgentConversationFolder;
  folders: AgentConversationFolder[];
}

export interface AgentConversationFolderPayload {
  name: string;
  sortOrder?: number | null;
}

export interface AgentConversationMovePayload {
  folderId?: string | null;
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
