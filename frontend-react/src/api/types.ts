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
  createdAt?: string;
  updatedAt?: string;
}

export interface RagEvidence {
  evidenceId: string;
  documentId: string;
  title: string;
  snippet: string;
  source: string;
  sectionName: string;
  documentType: string;
  score: number;
}

export interface RagQueryResult {
  answer: string;
  expandedQueries: string[];
  evidences: RagEvidence[];
}

