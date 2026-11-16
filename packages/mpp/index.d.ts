import { Request, Response, NextFunction } from 'express';

export interface RequireScoreOptions {
  minScore?: number;
  allowUnregistered?: boolean;
  /** Behavior when MolTrust API is unreachable. Default: 'open' */
  failBehavior?: 'open' | 'closed';
  onDeny?: (req: Request, res: Response, info: {
    wallet: string;
    score: number;
    minScore: number;
  }) => void;
}

export function requireScore(options?: RequireScoreOptions): (
  req: Request,
  res: Response,
  next: NextFunction
) => Promise<void>;

export function getScore(wallet: string): Promise<number | null>;
export function extractWalletFromMPP(req: Request): string | null;
