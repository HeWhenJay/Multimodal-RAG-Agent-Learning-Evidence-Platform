export interface Result<T> {
  code: number;
  msg: string | null;
  data: T;
}

export interface RagOverview {
  materialCount: number;
  chunkCount: number;
  evidenceCount: number;
  lastIndexedTitle?: string | null;
}

export interface LearningMaterial {
  id: number;
  title: string;
  documentType: string;
  source: string;
  status: string;
  parser?: string | null;
  documentSummary?: string | null;
  chunkCount: number;
  originalFilename?: string | null;
  originalFilePath?: string | null;
  createdAt?: string;
  updatedAt?: string;
}

export interface RagEvidence {
  evidenceId: string;
  documentId: string;
  documentTitle?: string | null;
  blockId?: string | null;
  blockType?: string | null;
  pageIndex?: number | null;
  slideIndex?: number | null;
  sheetName?: string | null;
  cellRange?: string | null;
  sectionTitle?: string | null;
  title: string;
  snippet: string;
  source: string;
  sourcePath?: string | null;
  assetPath?: string | null;
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
}
