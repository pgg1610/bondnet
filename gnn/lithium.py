# pylint: disable=no-member
import sys
import time
import torch
from torch.optim.lr_scheduler import ReduceLROnPlateau
from gnn.data.dataset import train_validation_test_split
from gnn.data.electrolyte import ElectrolyteDataset
from gnn.data.dataloader import DataLoader
from gnn.model.hgat import HGAT
from gnn.metric import MSELoss, MAELoss, evaluate, EarlyStopping
from gnn.args import create_parser
from gnn.utils import pickle_dump

torch.manual_seed(35)

args = create_parser()
if args.gpu >= 0 and torch.cuda.is_available():
    args.device = torch.device("cuda")
else:
    args.device = None

# dataset
sdf_file = "/Users/mjwen/Applications/mongo_db_access/extracted_data/sturct_n200.sdf"
label_file = "/Users/mjwen/Applications/mongo_db_access/extracted_data/label_n200.txt"
dataset = ElectrolyteDataset(sdf_file, label_file)
trainset, valset, testset = train_validation_test_split(dataset, validation=0.1, test=0.1)
train_loader = DataLoader(trainset, batch_size=10, shuffle=True)

# model
attn_mechanism = {
    "atom": {"edges": ["b2a", "g2a", "a2a"], "nodes": ["bond", "global", "atom"]},
    "bond": {"edges": ["a2b", "g2b", "b2b"], "nodes": ["atom", "global", "bond"]},
    "global": {"edges": ["a2g", "b2g", "g2g"], "nodes": ["atom", "bond", "global"]},
}
attn_order = ["atom", "bond", "global"]
in_feats = trainset.get_feature_size(attn_order)
model = HGAT(
    attn_mechanism,
    attn_order,
    in_feats,
    num_gat_layers=args.num_gat_layers,
    gat_hidden_size=args.gat_hidden_size,
    num_heads=args.num_heads,
    feat_drop=args.feat_drop,
    attn_drop=args.attn_drop,
    negative_slope=args.negative_slope,
    residual=args.residual,
    num_fc_layers=args.num_fc_layers,
    fc_hidden_size=args.fc_hidden_size,
)
print(model)
if args.device is not None:
    model.to(device=args.device)

# optimizer and loss
optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
loss_func = MSELoss()

# accuracy metric, learning rate scheduler, and stopper
metric = MAELoss()
patience = 100
scheduler = ReduceLROnPlateau(
    optimizer, mode="min", factor=0.1, patience=patience // 2, verbose=True
)
stopper = EarlyStopping(patience=patience)

print("\n\n# Epoch     Loss         TrainAcc        ValAcc     Time (s)")
t0 = time.time()

for epoch in range(args.epochs):
    ti = time.time()

    model.train()
    epoch_loss = 0
    epoch_pred = []
    epoch_energy = []
    epoch_indicator = []
    for it, (bg, label) in enumerate(train_loader):
        feats = {nt: bg.nodes[nt].data["feat"] for nt in attn_order}
        if args.device is not None:
            feats = {k: v.to(device=args.device) for k, v in feats.items()}
            label = {k: v.to(device=args.device) for k, v in label.items()}
        pred = model(bg, feats)
        loss = loss_func(pred, label["energies"], label["indicators"])
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        epoch_loss += loss.detach().item()

        # NOTE keep track of them for later accuracy evaluate. requires a lot of memory.
        # Alternatively, we can compute them later if memory is an issue. Should provide
        # a switch to choose between the two
        epoch_pred.append(pred)
        epoch_energy.append(label["energies"])
        epoch_indicator.append(label["indicators"])

    epoch_loss /= it + 1

    # evaluate the accuracy
    with torch.no_grad():
        train_acc = metric(
            torch.cat(epoch_pred), torch.cat(epoch_energy), torch.cat(epoch_indicator)
        )
    val_acc = evaluate(model, valset, metric, attn_order, args.device)
    scheduler.step(val_acc)
    if stopper.step(val_acc, model, msg="epoch " + str(epoch)):
        # save results for hyperparam tune
        pickle_dump(float(stopper.best_score), args.output_file)
        break
    tt = time.time() - ti

    print(
        "{:5d}   {:12.6e}   {:12.6e}   {:12.6e}   {:.2f}".format(
            epoch, epoch_loss, train_acc, val_acc, tt
        )
    )
    if epoch % (max(args.epochs // 10, 1)) == 0:
        sys.stdout.flush()

# save results for hyperparam tune
pickle_dump(float(stopper.best_score), args.output_file)

test_acc = evaluate(model, testset, metric, attn_order, args.device)
tt = time.time() - t0
print("\n#TestAcc: {:12.6e} | Total time (s): {:.2f}\n".format(test_acc, tt))
