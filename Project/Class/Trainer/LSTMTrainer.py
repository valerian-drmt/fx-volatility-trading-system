import torch
import numpy as np
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score, hamming_loss

# 🔧 Config import
import os

logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

class LSTMTrainer:
    def __init__(self, model, preprocessing, criterion, optimizer, scheduler=None, patience=5):
        self.model = model
        self.preprocessing = preprocessing
        self.device = preprocessing.device
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.patience = patience
        self.best_val_loss = float('inf')
        self.epochs_no_improve = 0
        self.early_stop = False
        self.history = []  # Store metrics for each epoch

    def train(self, num_epochs=50):
        for epoch in range(num_epochs):
            train_loss = self._train_one_epoch()
            val_loss, metrics = self._validate()

            if self.scheduler:
                self.scheduler.step()

            print(
                f"Epoch {epoch + 1:02d} | Train Loss: {train_loss:.4f} | Val Loss: {val_loss:.4f} "
                f"| F1: {metrics['f1']:.4f} | Precision: {metrics['precision']:.4f} | Recall: {metrics['recall']:.4f} "
                f"| AUC: {metrics['auc']:.4f} | ExactMatch: {metrics['exact_match']:.4f} | HammingLoss: {metrics['hamming']:.4f}")

            # Save metrics history
            self.history.append({
                'epoch': epoch + 1,
                'train_loss': train_loss,
                'val_loss': val_loss,
                **metrics
            })

            if val_loss < self.best_val_loss:
                self.best_val_loss = val_loss
                self.epochs_no_improve = 0
                torch.save(self.model.state_dict(), "best_model.pt")
            else:
                self.epochs_no_improve += 1
                if self.epochs_no_improve >= self.patience:
                    print("Early stopping triggered.")
                    self.early_stop = True
                    break

    def _train_one_epoch(self):
        self.model.train()
        running_loss = 0
        for x_batch, y_batch in self.preprocessing.train_loader:
            x_batch, y_batch = x_batch.to(self.device), y_batch.to(self.device)

            self.optimizer.zero_grad()
            outputs = self.model(x_batch)
            loss = self.criterion(outputs, y_batch.float())
            loss.backward()
            self.optimizer.step()

            running_loss += loss.item() * x_batch.size(0)

        return running_loss / len(self.preprocessing.train_loader.dataset)

    def _validate(self):
        self.model.eval()
        running_loss = 0
        all_preds = []
        all_targets = []
        all_probs = []

        with torch.no_grad():
            for x_batch, y_batch in self.preprocessing.val_loader:
                x_batch, y_batch = x_batch.to(self.device), y_batch.to(self.device)
                outputs = self.model(x_batch)
                loss = self.criterion(outputs, y_batch.float())
                running_loss += loss.item() * x_batch.size(0)

                probs = torch.sigmoid(outputs)
                preds = probs > 0.5
                all_preds.append(preds.cpu().numpy())
                all_targets.append(y_batch.cpu().numpy())
                all_probs.append(probs.cpu().numpy())

        all_preds = np.vstack(all_preds)
        all_targets = np.vstack(all_targets)
        all_probs = np.vstack(all_probs)

        metrics = self.compute_metrics(all_targets, all_preds, all_probs)
        return running_loss / len(self.preprocessing.val_loader.dataset), metrics

    @staticmethod
    def compute_metrics(y_true, y_pred, y_probs):
        y_true_copy = y_true.copy()

        for col in range(y_true.shape[1]):
            unique_vals = np.unique(y_true[:, col])
            if len(unique_vals) == 1:
                # Inject the missing class artificially
                if unique_vals[0] == 0:
                    y_true_copy[0, col] = 1
                else:
                    y_true_copy[0, col] = 0

        try:
            auc = roc_auc_score(y_true_copy, y_probs, average='macro')
        except ValueError:
            auc = float('nan')

        metrics = {
            "f1": f1_score(y_true, y_pred, average='macro', zero_division=0),
            "precision": precision_score(y_true, y_pred, average='macro', zero_division=0),
            "recall": recall_score(y_true, y_pred, average='macro', zero_division=0),
            "auc": auc,
            "exact_match": np.mean(np.all(y_true == y_pred, axis=1)),
            "hamming": hamming_loss(y_true, y_pred)
        }
        return metrics





