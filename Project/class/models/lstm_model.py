import torch.nn as nn

# 🔧 config import
import os

logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

class LSTMModel(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim, num_layers, dropout=0.3):
        super(LSTMModel, self).__init__()

        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0
        )

        self.norm = nn.BatchNorm1d(hidden_dim)

        self.fc_layers = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.LeakyReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, output_dim)
        )

    def forward(self, x):
        lstm_out, _ = self.lstm(x)  # lstm_out: [batch, seq_len, hidden_dim]
        last_hidden = lstm_out[:, -1, :]  # [batch, hidden_dim]
        normed = self.norm(last_hidden)  # Apply BatchNorm
        logits = self.fc_layers(normed)  # Pass through MLP
        return logits
